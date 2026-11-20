"""A strict, local stand-in for the GenLayer GenVM Python SDK.

Purpose
-------
Run `contracts/redline.py` natively in plain CPython, with no downloads, so
the whole contract test-suite works offline and in a few seconds.

It is deliberately *stricter* than the real SDK in the places where contract
bugs usually hide, so a contract that passes here is unlikely to trip over
those rules on the network:

* storage (contract fields, TreeMap, DynArray, storage dataclasses) cannot be
  read or written inside a non-deterministic block (leader / validator code);
* `view` methods cannot write storage;
* integers that live in storage must be explicit `u256(...)` values; negative
  or oversized values raise instead of wrapping silently;
* assigning a dataclass into storage COPIES it, so mutating the local object
  afterwards (a classic bug) does not change what was stored;
* a transaction that raises is rolled back completely (state and events);
* values returned by `view` methods and by leaders must be calldata-encodable
  (None, bool, int, str, bytes, Address, list, dict[str, ...]); floats, sets and
  tuples are rejected;
* `gl.nondet.*` (web / LLM) may only be called inside non-deterministic blocks;
* a contract may only assign declared storage fields.

It is NOT the real GenVM: it does not model gas, calldata size limits, leader
rotation or real consensus. The live end-to-end run is the final authority.
"""
from __future__ import annotations

import copy
import dataclasses
import functools
import json
import typing
from types import SimpleNamespace

__all__ = ["gl", "u256", "Address", "TreeMap", "DynArray", "allow_storage", "Keccak256"]


# --------------------------------------------------------------------------
# execution context (one per process; the harness resets it)
# --------------------------------------------------------------------------
class _Ctx:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.mode = "idle"  # idle | write | view | nondet
        self.sender: typing.Optional["Address"] = None
        self.raw: dict = {}
        self.events: list = []
        self.web_handler: typing.Optional[typing.Callable] = None
        self.llm_handler: typing.Optional[typing.Callable] = None
        self.captured: list = []
        self.auto_validate = True
        self.nondet_calls = 0


CTX = _Ctx()


class ConsensusFailure(Exception):
    """The (honest) validators did not accept the leader's result."""


def _need_readable(what: str) -> None:
    if CTX.mode == "nondet":
        raise RuntimeError(f"storage read inside a non-deterministic block: {what}")


def _need_writable(what: str) -> None:
    if CTX.mode == "nondet":
        raise RuntimeError(f"storage write inside a non-deterministic block: {what}")
    if CTX.mode == "view":
        raise RuntimeError(f"storage write inside a view method: {what}")


# --------------------------------------------------------------------------
# scalar types
# --------------------------------------------------------------------------
class u256(int):
    """Unsigned 256-bit integer. Arithmetic returns plain ints on purpose:
    anything stored must be wrapped in u256(...) again."""

    __slots__ = ()

    def __new__(cls, value: int = 0) -> "u256":
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"u256 needs an int, got {type(value).__name__}")
        if not 0 <= int(value) < 2**256:
            raise OverflowError(f"{int(value)} does not fit in u256")
        return super().__new__(cls, int(value))


class Address:
    __slots__ = ("_b",)

    def __init__(self, value: typing.Any) -> None:
        if isinstance(value, Address):
            raw = value._b
        elif isinstance(value, (bytes, bytearray)) and len(value) == 20:
            raw = bytes(value)
        elif isinstance(value, str) and value[:2].lower() == "0x" and len(value) == 42:
            try:
                raw = bytes.fromhex(value[2:])
            except ValueError:
                raise ValueError(f"not an address: {value!r}")
        else:
            raise ValueError(f"not an address: {value!r}")
        object.__setattr__(self, "_b", raw)

    @property
    def as_bytes(self) -> bytes:
        return self._b

    @property
    def as_hex(self) -> str:
        return "0x" + self._b.hex()

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Address) and other._b == self._b

    def __hash__(self) -> int:
        return hash(self._b)

    def __repr__(self) -> str:
        return f"Address({self.as_hex})"


# ---- Keccak-256 (the Ethereum flavour, NOT NIST SHA3) ----------------------
_M64 = (1 << 64) - 1
_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_ROT = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]


def _rol(v: int, n: int) -> int:
    n %= 64
    return ((v << n) | (v >> (64 - n))) & _M64 if n else v


def _keccak_f(s: list) -> None:
    for rnd in range(24):
        c = [s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                s[x + 5 * y] ^= d[x]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol(s[x + 5 * y], _ROT[x][y])
        for x in range(5):
            for y in range(5):
                s[x + 5 * y] = b[x + 5 * y] ^ ((~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y])
        s[0] ^= _RC[rnd]


@functools.lru_cache(maxsize=8192)
def _keccak256(data: bytes) -> bytes:
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate:
        padded.append(0)
    padded[-1] |= 0x80
    state = [0] * 25
    for off in range(0, len(padded), rate):
        block = padded[off:off + rate]
        for i in range(rate // 8):
            state[i] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        _keccak_f(state)
    return b"".join(state[i].to_bytes(8, "little") for i in range(4))


class Keccak256:
    def __init__(self) -> None:
        self._buf = bytearray()

    def update(self, data: bytes) -> None:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("Keccak256.update needs bytes")
        self._buf += data

    def digest(self) -> bytes:
        return _keccak256(bytes(self._buf))  # pure function of the input, so caching is safe

    def hexdigest(self) -> str:
        return self.digest().hex()


# --------------------------------------------------------------------------
# storage typing helpers
# --------------------------------------------------------------------------
_K = typing.TypeVar("_K")
_V = typing.TypeVar("_V")
_T = typing.TypeVar("_T")


def _is_storage_dc(tp: typing.Any) -> bool:
    return isinstance(tp, type) and getattr(tp, "_gl_storage", False)


def _conform(value: typing.Any, tp: typing.Any, what: str) -> typing.Any:
    origin = typing.get_origin(tp)
    if origin in (TreeMap, DynArray):
        raise TypeError(f"{what}: containers cannot be assigned, mutate them in place")
    if tp is u256:
        if not isinstance(value, u256):
            raise TypeError(f"{what}: expected u256, got {type(value).__name__} (wrap it in u256(...))")
        return value
    if tp is str:
        if not isinstance(value, str):
            raise TypeError(f"{what}: expected str, got {type(value).__name__}")
        return value
    if tp is bool:
        if not isinstance(value, bool):
            raise TypeError(f"{what}: expected bool, got {type(value).__name__}")
        return value
    if tp is Address:
        if not isinstance(value, Address):
            raise TypeError(f"{what}: expected Address, got {type(value).__name__}")
        return value
    if _is_storage_dc(tp):
        if not isinstance(value, tp):
            raise TypeError(f"{what}: expected {tp.__name__}, got {type(value).__name__}")
        return copy.deepcopy(value)  # storing COPIES the object
    raise TypeError(f"{what}: unsupported storage type {tp!r}")


def _default(tp: typing.Any) -> typing.Any:
    origin = typing.get_origin(tp)
    if origin is TreeMap:
        k, v = typing.get_args(tp)
        return TreeMap(k, v)
    if origin is DynArray:
        (t,) = typing.get_args(tp)
        return DynArray(t)
    if tp is u256:
        return u256(0)
    if tp is str:
        return ""
    if tp is bool:
        return False
    if tp is Address:
        return Address(b"\x00" * 20)
    raise TypeError(f"no default for storage type {tp!r}")


class TreeMap(typing.Generic[_K, _V]):
    def __init__(self, key_t: typing.Any = None, val_t: typing.Any = None) -> None:
        self._k = key_t
        self._v = val_t
        self._d: dict = {}

    def _key(self, key: typing.Any) -> typing.Any:
        return _conform(key, self._k, "TreeMap key")

    def get(self, key: typing.Any, default: typing.Any = None) -> typing.Any:
        _need_readable("TreeMap.get")
        return self._d.get(self._key(key), default)

    def __getitem__(self, key: typing.Any) -> typing.Any:
        _need_readable("TreeMap[...]")
        return self._d[self._key(key)]

    def __contains__(self, key: typing.Any) -> bool:
        _need_readable("TreeMap.__contains__")
        return self._key(key) in self._d

    def __setitem__(self, key: typing.Any, value: typing.Any) -> None:
        _need_writable("TreeMap[...] =")
        self._d[self._key(key)] = _conform(value, self._v, "TreeMap value")

    def get_or_insert_default(self, key: typing.Any) -> typing.Any:
        _need_writable("TreeMap.get_or_insert_default")
        k = self._key(key)
        if k not in self._d:
            self._d[k] = _default(self._v)
        return self._d[k]


class DynArray(typing.Generic[_T]):
    def __init__(self, elem_t: typing.Any = None) -> None:
        self._t = elem_t
        self._a: list = []

    def append(self, value: typing.Any) -> None:
        _need_writable("DynArray.append")
        self._a.append(_conform(value, self._t, "DynArray element"))

    def __getitem__(self, index: int) -> typing.Any:
        _need_readable("DynArray[...]")
        if isinstance(index, slice) or not isinstance(index, int):
            raise TypeError("DynArray only supports integer indexing")
        return self._a[index]

    def __len__(self) -> int:
        _need_readable("len(DynArray)")
        return len(self._a)

    def __iter__(self) -> typing.Iterator:
        _need_readable("iter(DynArray)")
        return iter(list(self._a))


def allow_storage(cls: type) -> type:
    """Mark a dataclass as storable and enforce field types / access rules."""
    hints = typing.get_type_hints(cls)
    field_names = {f.name for f in dataclasses.fields(cls)}
    cls._gl_storage = True
    cls._gl_hints = {n: hints[n] for n in field_names}

    def __setattr__(self, name: str, value: typing.Any) -> None:
        if name not in field_names:
            raise AttributeError(f"{cls.__name__} has no storage field {name!r}")
        _need_writable(f"{cls.__name__}.{name} =")
        object.__setattr__(self, name, _conform(value, cls._gl_hints[name], f"{cls.__name__}.{name}"))

    def __getattribute__(self, name: str) -> typing.Any:
        if CTX.mode == "nondet" and name in field_names:
            raise RuntimeError(f"storage read inside a non-deterministic block: {cls.__name__}.{name}")
        return object.__getattribute__(self, name)

    cls.__setattr__ = __setattr__
    cls.__getattribute__ = __getattribute__
    return cls


# --------------------------------------------------------------------------
# calldata
# --------------------------------------------------------------------------
class Rec(dict):
    """A dict that also allows attribute access (how views return dataclasses)."""

    def __getattr__(self, name: str) -> typing.Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


def to_calldata(v: typing.Any, path: str = "value") -> typing.Any:
    if v is None or isinstance(v, (bool, str, bytes)):
        return v
    if isinstance(v, int):
        return int(v)
    if isinstance(v, Address):
        return v
    if _is_storage_dc(type(v)):
        return Rec({f.name: to_calldata(object.__getattribute__(v, f.name), f"{path}.{f.name}") for f in dataclasses.fields(v)})
    if isinstance(v, (list, DynArray)):
        return [to_calldata(x, f"{path}[{i}]") for i, x in enumerate(list(v) if isinstance(v, list) else list(v._a))]
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if not isinstance(k, str):
                raise TypeError(f"{path}: dict keys must be str, got {type(k).__name__}")
            out[k] = to_calldata(x, f"{path}.{k}")
        return out
    raise TypeError(f"{path}: {type(v).__name__} is not calldata-encodable")


# --------------------------------------------------------------------------
# the `gl` namespace
# --------------------------------------------------------------------------
class _UserError(Exception):
    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message


class _VMReturn:
    def __init__(self, calldata: typing.Any) -> None:
        self.calldata = calldata


def _run_nondet_unsafe(leader_fn: typing.Callable, validator_fn: typing.Callable) -> typing.Any:
    if CTX.mode != "write":
        raise RuntimeError("run_nondet_unsafe is only allowed in write methods")
    CTX.mode = "nondet"
    try:
        leader_result = to_calldata(leader_fn(), "leader result")
    finally:
        CTX.mode = "write"
    CTX.captured.append((copy.deepcopy(leader_result), validator_fn))
    if CTX.auto_validate:
        CTX.mode = "nondet"
        try:
            ok = validator_fn(_VMReturn(copy.deepcopy(leader_result)))
        finally:
            CTX.mode = "write"
        if ok is not True:
            raise ConsensusFailure("an honest validator rejected the honest leader's result")
    return copy.deepcopy(leader_result)


def _need_nondet(api: str) -> None:
    if CTX.mode != "nondet":
        raise RuntimeError(f"{api} may only be called inside a non-deterministic block")


def _web_render(url: str, *, mode: str = "text", wait_after_loaded: typing.Optional[str] = None) -> str:
    _need_nondet("gl.nondet.web.render")
    if mode not in ("text", "html", "screenshot"):
        raise ValueError(f"bad render mode {mode!r}")
    CTX.nondet_calls += 1
    if CTX.web_handler is None:
        raise Exception("no web handler installed")
    out = CTX.web_handler(url, mode)
    if not isinstance(out, str):
        raise TypeError("web handler must return str")
    return out


def _exec_prompt(prompt: str, *, response_format: str = "text", images: typing.Any = None) -> typing.Any:
    _need_nondet("gl.nondet.exec_prompt")
    if not isinstance(prompt, str):
        raise TypeError("prompt must be a str")
    CTX.nondet_calls += 1
    if CTX.llm_handler is None:
        raise Exception("no LLM handler installed")
    out = CTX.llm_handler(prompt, response_format)
    if response_format == "json":
        if isinstance(out, str):
            out = json.loads(out)  # invalid JSON raises, like the real call
        return copy.deepcopy(out)
    return out if isinstance(out, str) else json.dumps(out)


class _Event:
    def __new__(cls, *args: typing.Any, **kwargs: typing.Any) -> "_Event":
        import inspect

        obj = super().__new__(cls)
        sig = inspect.signature(cls.__init__)
        sig.bind(obj, *args, **kwargs)  # arity / keyword check
        hints = typing.get_type_hints(cls.__init__)
        positional = [p for p in list(sig.parameters.values())[1:] if p.kind == p.POSITIONAL_ONLY]
        for p, a in zip(positional, args):
            if p.name in hints:
                _conform(a, hints[p.name], f"event {cls.__name__}.{p.name}")
        obj._args = args
        obj._kwargs = to_calldata(dict(kwargs), f"event {cls.__name__} blob")
        return obj

    def emit(self) -> None:
        if CTX.mode != "write":
            raise RuntimeError("events can only be emitted from write methods")
        CTX.events.append((type(self).__name__, tuple(int(a) if isinstance(a, int) else a for a in self._args), dict(self._kwargs)))


class _ContractBase:
    _gl_fields: typing.Dict[str, typing.Any] = {}

    def __init_subclass__(cls, **kw: typing.Any) -> None:
        super().__init_subclass__(**kw)
        hints = typing.get_type_hints(cls)
        cls._gl_fields = {k: v for k, v in hints.items() if not k.startswith("_")}

    def __getattribute__(self, name: str) -> typing.Any:
        if name in type(self)._gl_fields:
            _need_readable(f"self.{name}")
            return object.__getattribute__(self, "_storage")[name]
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: typing.Any) -> None:
        fields = type(self)._gl_fields
        if name not in fields:
            raise AttributeError(f"{type(self).__name__} has no declared storage field {name!r}")
        _need_writable(f"self.{name} =")
        object.__getattribute__(self, "_storage")[name] = _conform(value, fields[name], f"self.{name}")


class _Public:
    @staticmethod
    def write(fn: typing.Callable) -> typing.Callable:
        fn._gl_kind = "write"
        return fn

    @staticmethod
    def view(fn: typing.Callable) -> typing.Callable:
        fn._gl_kind = "view"
        return fn


class _Message:
    @property
    def sender_address(self) -> Address:
        if CTX.sender is None:
            raise RuntimeError("no sender in this context")
        return CTX.sender


class _MessageRaw:
    def get(self, key: str, default: typing.Any = None) -> typing.Any:
        return CTX.raw.get(key, default)

    def __getitem__(self, key: str) -> typing.Any:
        return CTX.raw[key]


gl = SimpleNamespace(
    Contract=_ContractBase,
    Event=_Event,
    public=_Public,
    message=_Message(),
    message_raw=_MessageRaw(),
    vm=SimpleNamespace(
        UserError=_UserError,
        Return=_VMReturn,
        run_nondet_unsafe=_run_nondet_unsafe,
    ),
    nondet=SimpleNamespace(
        web=SimpleNamespace(render=_web_render),
        exec_prompt=_exec_prompt,
    ),
)
