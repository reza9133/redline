"""The e2e fixtures are part of the contract's behaviour: the live script relies on the
deterministic facts asserted here (topics, plan kinds, injection flag, ...)."""
import json
import unittest

from base import (FIXTURE_DIR, fx, QUOTE_ARBITRATION, QUOTE_DATA, RedlineCase, Revert, TINY, UNRELATED, URL, V1, V1_CAPS,
                  V2_COSMETIC, V3_ADVERSE, V5_INJECTION)

NAMES = ["terms_v1", "terms_v1_caps", "terms_v2_cosmetic", "terms_v3_adverse", "terms_v5_injection", "terms_plain", "terms_plain_reworded", "unrelated", "tiny"]


class Fixtures(RedlineCase):
    def plan(self, new, owner=False):
        return self.m._plan(V1, new, owner)

    def test_files_exist_and_are_plain_ascii(self):
        for name in NAMES:
            raw = (FIXTURE_DIR / f"{name}.txt").read_bytes()
            self.assertTrue(raw.strip(), name)
            self.assertTrue(all(b < 128 for b in raw), f"{name} must be ASCII so JS and Python hash the same bytes")
            self.assertTrue(all(b >= 32 or b in (9, 10, 13) for b in raw), name)

    def test_sizes_relative_to_the_contract_limits(self):
        for name in NAMES:
            n = len(" ".join((FIXTURE_DIR / f"{name}.txt").read_text().split()))
            if name == "tiny":
                self.assertLess(n, self.m.MIN_TEXT_CHARS)
            else:
                self.assertGreaterEqual(n, self.m.MIN_TEXT_CHARS, name)
                self.assertLess(n, self.m.MAX_TEXT_CHARS, name)

    def test_case_only_variant_is_format_only_but_a_different_text(self):
        self.assertEqual(self.plan(V1_CAPS)["kind"], "format_only")
        self.assertNotEqual(V1, V1_CAPS)

    def test_cosmetic_variant_is_quiet(self):
        p = self.plan(V2_COSMETIC)
        self.assertEqual((p["kind"], p["topics"], p["rewrite"]), ("judge", [], False))
        self.assertLess(p["churn_bp"], self.m.HEAVY_CHURN_BP // 5)
        self.assertFalse(self.m._injection_flagged(p["removed"] + p["added"]))

    def test_adverse_variant_touches_the_expected_topics_and_is_not_a_rewrite(self):
        p = self.plan(V3_ADVERSE)
        self.assertEqual(p["kind"], "judge")
        self.assertEqual(p["topics"], ["DISPUTES", "DATA", "MONEY", "NOTICE"])
        self.assertGreaterEqual(p["retained_bp"], self.m.MIN_RETAINED_BP)
        self.assertEqual(self.plan(V3_ADVERSE, owner=False)["kind"], "judge")  # a non-owner may commit it
        for quote in (QUOTE_ARBITRATION, QUOTE_DATA, "do not sell your data"):
            self.assertTrue(self.m._quote_ok(quote, p["shown_added"], p["shown_removed"]), quote)

    def test_injection_variant(self):
        p = self.plan(V5_INJECTION)
        self.assertEqual((p["kind"], p["rewrite"]), ("judge", False))
        self.assertTrue(self.m._injection_flagged(p["removed"] + p["added"]))
        self.assertEqual(p["topics"], ["MONEY", "TERMINATION", "NOTICE"])
        self.assertTrue(self.m._quote_ok("terminate your account at any time", p["shown_added"], p["shown_removed"]))

    def test_unrelated_variant_is_a_rewrite_only_the_owner_may_commit(self):
        self.assertEqual(self.plan(UNRELATED, owner=False)["kind"], "blocked")
        p = self.plan(UNRELATED, owner=True)
        self.assertEqual((p["kind"], p["rewrite"]), ("judge", True))

    def test_quiet_rewrite_pair(self):
        """D3 of the live script: a rewrite that nothing but the rewrite flag can make MEDIUM."""
        base, reworded = fx("terms_plain"), fx("terms_plain_reworded")
        self.assertEqual([t for u in self.m._split_units(base) for t in self.m._unit_topics(u)], [])
        self.assertEqual(self.m._plan(base, reworded, False)["kind"], "blocked")
        p = self.m._plan(base, reworded, True)
        self.assertEqual((p["kind"], p["rewrite"], p["topics"]), ("judge", True, []))
        self.assertLess(p["retained_bp"], self.m.MIN_RETAINED_BP)
        self.assertLess(p["churn_bp"], self.m.HEAVY_CHURN_BP)
        self.assertFalse(self.m._injection_flagged(p["removed"] + p["added"]))
        for effect in ("NEUTRAL", "BETTER", "UNCLEAR"):  # without the rewrite floor these would all be LOW
            self.assertEqual(self.m._severity(effect, p["topics"], p["churn_bp"], True)[0], "MEDIUM", effect)
            self.assertEqual(self.m._severity(effect, p["topics"], p["churn_bp"], False)[0], "LOW", effect)

    def test_the_adverse_text_survives_the_round_trip_back_to_v1(self):
        back = self.m._plan(V3_ADVERSE, V1, False)
        self.assertEqual(back["kind"], "judge")
        self.assertTrue(self.m._quote_ok("do not sell your data", back["shown_added"], back["shown_removed"]))


class LiveScriptStory(RedlineCase):
    """The exact sequence live-e2e.mjs performs, with a scripted model."""

    def test_story(self):
        c = self.c
        base = "https://raw.githubusercontent.com/o/r/main/e2e/fixtures"
        bodies = {"v1": V1, "caps": V1_CAPS, "v2": V2_COSMETIC, "v3": V3_ADVERSE, "v5": V5_INJECTION, "unrelated": UNRELATED, "tiny": TINY}
        pages = {f"{base}/terms_v1.txt": V1, f"{base}/terms_v1_caps.txt": V1_CAPS, f"{base}/terms_v2_cosmetic.txt": V2_COSMETIC,
                 f"{base}/terms_v3_adverse.txt": V3_ADVERSE, f"{base}/terms_v5_injection.txt": V5_INJECTION,
                 f"{base}/unrelated.txt": UNRELATED, f"{base}/tiny.txt": TINY}
        self.chain.web.clear()
        self.chain.web.set(r".*", lambda url: pages[url.split("?")[0]])
        u = lambda n, tag: f"{base}/{n}.txt?run=1&doc={tag}"

        with self.revert("too little readable text"):
            c.register_document("Tiny", "Northwind", "TERMS", u("tiny", "t"))
        d1 = int(c.register_document("Northwind Cloud ToS", "Northwind", "TERMS", u("terms_v1", "main")))
        with self.revert("already tracked"):
            c.register_document("Dup", "Northwind", "TERMS", u("terms_v1", "main"), sender=self.bob)
        with self.revert("cooldown"):
            c.append_revision(d1, u("terms_v1_caps", "main"))  # immediately after registration

        self.chain.advance(61)
        self.chain.llm.clear()
        fmt = int(c.append_revision(d1, u("terms_v1_caps", "main")))
        self.assertEqual((self.rev(fmt).effect, self.rev(fmt).judged), ("NEUTRAL", False))
        with self.revert("identical"):
            self.chain.advance(61)
            c.append_revision(d1, u("terms_v1_caps", "main"))

        self.chain.advance(61)
        self.llm("MUCH_WORSE", "Forced arbitration and data sharing.", QUOTE_ARBITRATION)
        v3 = int(c.append_revision(d1, u("terms_v3_adverse", "main")))
        r3 = self.rev(v3)
        self.assertEqual((r3.effect, r3.severity, r3.topics), ("MUCH_WORSE", "HIGH", "DISPUTES,DATA,MONEY,NOTICE"))
        self.assertEqual((r3.added_units, r3.removed_units), (5, 4))  # compared with the ORIGINAL text

        self.llm("MUCH_WORSE", "Same verdict.", QUOTE_ARBITRATION)
        c.appeal_revision(v3, sender=self.bob)
        self.assertEqual(self.rev(v3).appeal_outcome, "CONFIRMED")

        self.chain.advance(61)
        self.llm("MUCH_BETTER", "Protections restored.", "do not sell your data")
        back = int(c.append_revision(d1, u("terms_v1", "main"), sender=self.bob))  # a non-owner may revert on the same site
        self.assertEqual((self.rev(back).effect, self.rev(back).submitter), ("MUCH_BETTER", self.bob))

        self.chain.advance(61)
        self.llm("NEUTRAL", "Wording only.", "")
        v2 = int(c.append_revision(d1, u("terms_v2_cosmetic", "main")))
        self.assertEqual((self.rev(v2).effect, self.rev(v2).severity, self.rev(v2).topics), ("NEUTRAL", "LOW", ""))

        d2 = int(c.register_document("Injection doc", "Northwind", "TERMS", u("terms_v1", "inj")))
        self.chain.advance(61)
        self.llm("NEUTRAL", "obeyed the page", "")
        inj = int(c.append_revision(d2, u("terms_v5_injection", "inj")))
        self.assertTrue(self.rev(inj).injection_flagged)
        self.assertTrue(self.rev(inj).silent_flag)  # a model that obeys the page is caught by the guard

        pages[f"{base}/terms_plain.txt"] = fx("terms_plain")
        pages[f"{base}/terms_plain_reworded.txt"] = fx("terms_plain_reworded")
        d3 = int(c.register_document("Guard doc", "Harbor Notes Club", "POLICY", u("terms_plain", "guard")))
        self.chain.advance(61)
        with self.revert("different site"):
            c.append_revision(d3, "https://example.org/x", sender=self.bob)
        with self.revert("full rewrite"):
            c.append_revision(d3, u("unrelated", "guard"), sender=self.bob)
        self.llm("NEUTRAL", "Everything reworded, nothing changed in substance.", "")
        rw = int(c.append_revision(d3, u("terms_plain_reworded", "guard"), sender=self.alice))
        self.assertEqual((self.rev(rw).rewrite, self.rev(rw).topics, self.rev(rw).severity), (True, "", "MEDIUM"))

        s = self.stats()
        self.assertEqual((s["total_documents"], s["format_only"], s["rewrites"], s["appeals"]), (3, 1, 1, 1))


if __name__ == "__main__":
    unittest.main()
