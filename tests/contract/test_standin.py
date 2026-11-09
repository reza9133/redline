"""The harness must really be strict, or every other test proves nothing."""
import json
import typing
import unittest
from dataclasses import dataclass

from base import RedlineCase, Revert, URL, V1

import genlayer as sdk
from genlayer import Address, DynArray, Keccak256, TreeMap, allow_storage, gl, u256


def keccak(data: bytes) -> str:
    h = Keccak256()
    h.update(data)
    return h.hexdigest()


class KeccakVectors(unittest.TestCase):
    def test_known_vectors(self):
        self.assertEqual(keccak(b""), "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")
        self.assertEqual(keccak(b"abc"), "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45")
        self.assertEqual(
            keccak(b"The quick brown fox jumps over the lazy dog"),
            "4d741b6f1eb29cb2a9b9911c82f56fa8d73b04959d3d9d222895df6c0b28aa15",
        )

    def test_multi_block_matches_sha3_with_the_sha3_domain_byte(self):
        import hashlib
        import os

        def sponge(data: bytes, suffix: int) -> bytes:
            rate, d = 136, bytearray(data)
            d.append(suffix)
            while len(d) % rate:
                d.append(0)
            d[-1] |= 0x80
            st = [0] * 25
            for off in range(0, len(d), rate):
                for i in range(rate // 8):
                    st[i] ^= int.from_bytes(d[off + 8 * i:off + 8 * i + 8], "little")
                sdk._keccak_f(st)
            return b"".join(st[i].to_bytes(8, "little") for i in range(4))

        for n in (0, 1, 135, 136, 137, 272, 8000):
            blob = os.urandom(n)
            self.assertEqual(sponge(blob, 0x06), hashlib.sha3_256(blob).digest())


class ScalarStrictness(unittest.TestCase):
    def test_u256_range_and_type(self):
        self.assertEqual(u256(5), 5)
        for bad in (-1, 2**256, 1.5, "5", True, None):
            with self.assertRaises((TypeError, OverflowError)):
                u256(bad)

    def test_u256_arithmetic_returns_plain_int(self):
        self.assertNotIsInstance(u256(2) + 1, u256)

    def test_address(self):
        a = Address("0x" + "ab" * 20)
        self.assertEqual(a, Address(bytes.fromhex("ab" * 20)))
        self.assertEqual(a.as_hex, "0x" + "ab" * 20)
        for bad in ("0x12", "zz" * 20, 5, b"short"):
            with self.assertRaises((ValueError, TypeError)):
                Address(bad)


@allow_storage
@dataclass
class _Rec:
    n: u256
    s: str
    flag: bool


class StorageStrictness(RedlineCase):
    def test_plain_int_cannot_enter_a_u256_slot(self):
        with self.assertRaises(TypeError):
            _Rec(n=5, s="x", flag=False)
        rec = _Rec(n=u256(5), s="x", flag=False)
        with self.assertRaises(TypeError):
            rec.n = 6
        with self.assertRaises(AttributeError):
            rec.nope = 1

    def test_containers_check_their_element_types(self):
        arr = DynArray(u256)
        with self.assertRaises(TypeError):
            arr.append(3)
        arr.append(u256(3))
        m = TreeMap(str, u256)
        with self.assertRaises(TypeError):
            m["a"] = 1
        with self.assertRaises(TypeError):
            m[1] = u256(1)

    def test_storing_a_dataclass_copies_it(self):
        m = TreeMap(u256, _Rec)
        rec = _Rec(n=u256(1), s="a", flag=False)
        m[u256(1)] = rec
        rec.s = "changed after storing"
        self.assertEqual(m.get(u256(1)).s, "a")
        m.get(u256(1)).s = "changed through the live handle"
        self.assertEqual(m.get(u256(1)).s, "changed through the live handle")

    def test_storage_is_off_limits_inside_non_deterministic_blocks(self):
        m = self.m
        did = self.register()
        original = m.gl.vm.run_nondet_unsafe

        def leaky(leader, validator):
            return original(lambda: self.chain._obj.documents.get(u256(1)) and {"ok": True}, validator)

        m.gl.vm.run_nondet_unsafe = leaky
        try:
            self.page(V1 + " Something new here to change the text a lot.")
            self.llm()
            self.chain.advance(61)
            with self.assertRaisesRegex(RuntimeError, "non-deterministic"):
                self.c.append_revision(did, "https://example.com/terms")
        finally:
            m.gl.vm.run_nondet_unsafe = original

    def test_web_and_llm_only_inside_non_deterministic_blocks(self):
        with self.assertRaises(RuntimeError):
            gl.nondet.web.render("https://example.com", mode="text")
        with self.assertRaises(RuntimeError):
            gl.nondet.exec_prompt("hi", response_format="json")

    def test_views_cannot_write(self):
        self.register()
        cls = self.chain._cls
        original = cls.get_version

        @gl.public.view
        def bad_view(self_):
            self_.next_doc_id = u256(99)
            return "x"

        cls.get_version = bad_view
        try:
            with self.assertRaisesRegex(RuntimeError, "view"):
                self.c.get_version()
        finally:
            cls.get_version = original

    def test_undeclared_attributes_are_rejected(self):
        cls = self.chain._cls
        original = cls.get_version

        @gl.public.write
        def typo(self_):
            self_.next_dox_id = u256(1)

        cls.get_version = typo
        try:
            with self.assertRaises(AttributeError):
                self.c.get_version()
        finally:
            cls.get_version = original

    def test_views_cannot_return_floats_sets_or_tuples(self):
        for bad in (1.5, {1}, (1, 2), {"a": 1.0}, {1: "x"}):
            with self.assertRaises(TypeError):
                sdk.to_calldata(bad)

    def test_arguments_are_type_checked_like_calldata(self):
        with self.assertRaises(TypeError):
            self.c.get_document("1")
        with self.assertRaises((TypeError, OverflowError)):
            self.c.get_document(-1)
        with self.assertRaises(TypeError):
            self.c.get_document(True)

    def test_a_reverted_transaction_leaves_no_trace(self):
        self.register()
        before = self.stats()
        events = len(self.chain.events)
        with self.revert("already tracked"):
            self.c.register_document("Northwind ToS", "Northwind", "TERMS", URL)
        self.assertEqual(self.stats(), before)
        self.assertEqual(len(self.chain.events), events)
        self.assertEqual(self.register(url="https://example.com/other"), 2)  # ids were not consumed


if __name__ == "__main__":
    unittest.main()
