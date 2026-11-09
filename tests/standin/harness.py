"""A tiny local chain around the strict SDK stand-in (see genlayer/__init__.py).

    chain = Chain()                       # loads contracts/redline.py (or $REDLINE_CONTRACT)
    chain.web.set(r"example\\.com", "page text")
    chain.llm.set("WORSE", "summary", "quoted phrase")
    chain.c.register_document(...)        # write: rolled back completely on revert
    chain.c.get_stats()                   # view

`Revert` is raised when the contract raises gl.vm.UserError. Any other
exception (TypeError, KeyError, ...) is a contract bug and propagates as is.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import inspect
import itertools
import json
import os
import re
import sys
import typing
from pathlib import Path

STANDIN_DIR = Path(__file__).resolve().parent
if str(STANDIN_DIR) not in sys.path:
    sys.path.insert(0, str(STANDIN_DIR))

import genlayer as sdk  # noqa: E402  (the stand-in, found first on sys.path)
from genlayer import CTX, Address, ConsensusFailure, Rec, to_calldata, u256  # noqa: E402,F401

ROOT = STANDIN_DIR.parents[1]
CONTRACT_PATH = Path(os.environ.get("REDLINE_CONTRACT") or ROOT / "contracts" / "redline.py")
_module_counter = itertools.count(1)


class Revert(Exception):
    """The contract raised gl.vm.UserError(message)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def iso(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat().replace("+00:00", "Z")


class Web:
    """Regex -> page. A value may be a str, a callable(url) -> str, or an Exception to raise."""

    def __init__(self) -> None:
        self.rules: list = []
        self.calls: list = []

    def set(self, pattern: str, body: typing.Any) -> None:
        self.rules.insert(0, (pattern, body))  # newest rule wins

    def clear(self) -> None:
        self.rules.clear()

    def __call__(self, url: str, mode: str) -> str:
        self.calls.append(url)
        for pattern, value in self.rules:
            if re.search(pattern, url):
                if isinstance(value, BaseException):
                    raise value
                return value(url) if callable(value) else value
        raise Exception(f"no web mock for {url}")


class Llm:
    """Scripted model. `set` answers with strict JSON; `fn` takes full control."""

    def __init__(self) -> None:
        self.handler: typing.Optional[typing.Callable] = None
        self.calls: list = []

    def set(self, effect: str = "NEUTRAL", summary: str = "A one sentence summary.", quote: str = "") -> None:
        self.handler = lambda prompt: json.dumps({"effect": effect, "summary": summary, "quote": quote})

    def raw(self, text: typing.Any) -> None:
        self.handler = lambda prompt: text

    def fn(self, handler: typing.Callable) -> None:
        self.handler = handler

    def clear(self) -> None:
        self.handler = None

    def __call__(self, prompt: str, response_format: str) -> typing.Any:
        self.calls.append(prompt)
        if self.handler is None:
            raise Exception("no LLM script installed")
        out = self.handler(prompt)
        if isinstance(out, BaseException):
            raise out
        return out


class _Proxy:
    def __init__(self, chain: "Chain") -> None:
        self._chain = chain

    def __getattr__(self, name: str) -> typing.Callable:
        if name.startswith("_"):
            raise AttributeError(name)
        chain = self._chain

        def call(*args: typing.Any, sender: typing.Any = None) -> typing.Any:
            return chain.call(name, *args, sender=sender)

        return call


class Chain:
    START = 1_800_000_000  # a fixed, boring timestamp

    def __init__(self, path: typing.Optional[os.PathLike] = None) -> None:
        CTX.reset()
        self.web = Web()
        self.llm = Llm()
        CTX.web_handler = self.web
        CTX.llm_handler = self.llm
        self.alice = Address(b"\x01" * 20)
        self.bob = Address(b"\x02" * 20)
        self.carol = Address(b"\x03" * 20)
        self.sender = self.alice
        self.now = self.START
        self.raw_override: typing.Optional[dict] = None  # e.g. {} = a transaction without a timestamp
        self.path = Path(path or CONTRACT_PATH)
        self._cls = self._load(self.path)
        self.module = sys.modules[self._cls.__module__]  # the contract module, for pure-helper tests
        self._obj = self._deploy()
        self.c = _Proxy(self)

    # ------------------------------------------------------------ loading
    @staticmethod
    def _load(path: Path) -> type:
        name = f"redline_contract_{next(_module_counter)}"
        spec = importlib.util.spec_from_file_location(name, str(path))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        found = [
            v for v in vars(module).values()
            if isinstance(v, type) and issubclass(v, sdk.gl.Contract) and v is not sdk.gl.Contract
        ]
        assert len(found) == 1, f"expected exactly one contract class, found {found}"
        return found[0]

    def _deploy(self) -> typing.Any:
        cls = self._cls
        obj = cls.__new__(cls)
        object.__setattr__(obj, "_storage", {n: sdk._default(t) for n, t in cls._gl_fields.items()})
        CTX.sender = self.sender
        CTX.raw = {"datetime": iso(self.now)}
        CTX.mode = "write"
        try:
            cls.__init__(obj)
        finally:
            CTX.mode = "idle"
        return obj

    # ------------------------------------------------------------ switches
    @property
    def auto_validate(self) -> bool:
        return CTX.auto_validate

    @auto_validate.setter
    def auto_validate(self, value: bool) -> None:
        CTX.auto_validate = value

    @property
    def events(self) -> list:
        return list(CTX.events)

    # ------------------------------------------------------------ time
    def advance(self, seconds: int) -> None:
        self.now += int(seconds)

    def set_time(self, ts: int) -> None:
        self.now = int(ts)

    # ------------------------------------------------------------ calls
    def _arg(self, value: typing.Any, tp: typing.Any, fn: str, param: str) -> typing.Any:
        where = f"{fn}({param})"
        if tp is u256:
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{where}: u256 argument must be an int, got {type(value).__name__}")
            return u256(value)
        if tp is str:
            if not isinstance(value, str):
                raise TypeError(f"{where}: str argument must be a str, got {type(value).__name__}")
            return value
        if tp is Address:
            return value if isinstance(value, Address) else Address(value)
        return value

    def call(self, name: str, *args: typing.Any, sender: typing.Any = None) -> typing.Any:
        fn = getattr(self._cls, name, None)
        kind = getattr(fn, "_gl_kind", None)
        if kind is None:
            raise AttributeError(f"{name} is not a public contract method")
        params = list(inspect.signature(fn).parameters)[1:]
        if len(args) != len(params):
            raise TypeError(f"{name} takes {len(params)} arguments, got {len(args)}")
        hints = typing.get_type_hints(fn)
        converted = [self._arg(a, hints.get(p), name, p) for a, p in zip(args, params)]

        CTX.sender = sender if sender is not None else self.sender
        CTX.raw = self.raw_override if self.raw_override is not None else ({"datetime": iso(self.now)} if kind == "write" else {})
        storage = object.__getattribute__(self._obj, "_storage")
        backup = copy.deepcopy(storage) if kind == "write" else None
        events_before = len(CTX.events)
        CTX.mode = kind
        try:
            result = fn(self._obj, *converted)
        except sdk._UserError as exc:
            self._rollback(storage, backup, events_before)
            raise Revert(exc.message) from None
        except BaseException:
            self._rollback(storage, backup, events_before)
            raise
        finally:
            CTX.mode = "idle"
        return to_calldata(result, f"{name}() result")

    @staticmethod
    def _rollback(storage: dict, backup: typing.Optional[dict], events_before: int) -> None:
        if backup is not None:
            storage.clear()
            storage.update(backup)
        del CTX.events[events_before:]

    # ------------------------------------------------------------ validators
    def captured(self, index: int = -1) -> typing.Tuple[typing.Any, typing.Callable]:
        return CTX.captured[index]

    def leader_result(self, index: int = -1) -> typing.Any:
        return copy.deepcopy(CTX.captured[index][0])

    def run_validator(self, leader_result: typing.Any = None, *, raw: typing.Any = None, index: int = -1) -> bool:
        """Replay the captured validator against the CURRENT web / LLM mocks.

        leader_result: calldata the 'leader' returned (default: the real one)
        raw:           pass this object to the validator verbatim (e.g. a UserError)
        """
        original, validator = CTX.captured[index]
        if raw is not None:
            arg = raw
        else:
            arg = sdk.gl.vm.Return(copy.deepcopy(original if leader_result is None else leader_result))
        CTX.mode = "nondet"
        try:
            return bool(validator(arg))
        finally:
            CTX.mode = "idle"

    def clear_captured(self) -> None:
        CTX.captured.clear()
