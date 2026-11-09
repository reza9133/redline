"""Shared helpers for the Redline contract tests (stdlib unittest, no dependencies)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "standin"))

from harness import Chain, ConsensusFailure, Revert  # noqa: E402,F401

FIXTURE_DIR = ROOT / "e2e" / "fixtures"
URL = "https://example.com/terms"


def collapse(text: str) -> str:
    return " ".join(text.split())


def fx(name: str) -> str:
    """An e2e fixture, whitespace-collapsed the way the contract stores it."""
    return collapse((FIXTURE_DIR / f"{name}.txt").read_text())


V1 = fx("terms_v1")
V1_CAPS = fx("terms_v1_caps")
V2_COSMETIC = fx("terms_v2_cosmetic")
V3_ADVERSE = fx("terms_v3_adverse")
V5_INJECTION = fx("terms_v5_injection")
UNRELATED = fx("unrelated")
TINY = fx("tiny")

QUOTE_ARBITRATION = "binding arbitration"
QUOTE_DATA = "advertising partners"


def clauses(prefix: str, n: int) -> str:
    """n equal-length neutral sentences (no sensitive words): handy for exact ratio maths."""
    return " ".join(f"{prefix} clause {i:02d} is written here." for i in range(n))


class RedlineCase(unittest.TestCase):
    """A fresh chain per test plus short-hands for the common moves."""

    def setUp(self) -> None:
        self.chain = Chain()
        self.c = self.chain.c
        self.m = self.chain.module
        self.alice, self.bob, self.carol = self.chain.alice, self.chain.bob, self.chain.carol

    # ---- mocks ----------------------------------------------------------
    def page(self, body: str, pattern: str = r"example\.com") -> None:
        self.chain.web.clear()
        self.chain.web.set(pattern, body)

    def llm(self, effect: str = "NEUTRAL", summary: str = "A one sentence summary.", quote: str = "") -> None:
        self.chain.llm.set(effect, summary, quote)

    # ---- moves ----------------------------------------------------------
    def register(self, body: str = V1, name: str = "Northwind ToS", publisher: str = "Northwind",
                 kind: str = "TERMS", url: str = URL, sender=None) -> int:
        self.page(body)
        return int(self.c.register_document(name, publisher, kind, url, sender=sender))

    def append(self, doc: int = 1, body: str = None, effect: str = "NEUTRAL", quote: str = "",
               summary: str = "A one sentence summary.", url: str = URL, sender=None, wait: int = 61) -> int:
        if body is not None:
            self.page(body)
        self.llm(effect, summary, quote)
        if wait:
            self.chain.advance(wait)
        return int(self.c.append_revision(doc, url, sender=sender))

    def rev(self, rev_id: int):
        return self.c.get_revision(rev_id)

    def doc(self, doc_id: int = 1):
        return self.c.get_document(doc_id)

    def stats(self) -> dict:
        return dict(self.c.get_stats())

    def revert(self, text: str):
        return self.assertRaisesRegex(Revert, text)
