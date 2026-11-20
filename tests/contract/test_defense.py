"""Defence in depth.

Validators check a leader's result before consensus. The contract still re-derives and
re-checks everything it stores, so a bug or a malicious majority on the validator side cannot
write inconsistent data. These tests replace the consensus step by a FORGED result that
"consensus accepted" and check that the contract refuses (or neutralises) it."""
import unittest

from base import QUOTE_DATA, RedlineCase, V1, V3_ADVERSE


class Forged(RedlineCase):
    def forge(self, result):
        gl_vm = self.m.gl.vm
        original = gl_vm.run_nondet_unsafe
        gl_vm.run_nondet_unsafe = lambda leader, validator: result
        self.addCleanup(setattr, gl_vm, "run_nondet_unsafe", original)


class ForgedAppend(Forged):
    def setUp(self):
        super().setUp()
        self.register()
        self.chain.advance(61)
        self.digest = self.m._digest(V3_ADVERSE)

    def good(self, **over):
        result = {"ok": True, "kind": "judge", "hash": self.digest, "snapshot": V3_ADVERSE,
                  "effect": "WORSE", "summary": "s", "quote": QUOTE_DATA}
        result.update(over)
        return result

    def refused(self, result, pattern):
        self.forge(result)
        with self.revert(pattern):
            self.c.append_revision(1, "https://example.com/terms")
        self.assertEqual((self.doc(1).revision_count, self.stats()["total_revisions"]), (1, 1))

    def test_unusable_results(self):
        self.refused(self.good(snapshot=""), "unusable result")
        self.refused(self.good(kind="bogus"), "unusable result")
        self.refused(self.good(kind="unchanged"), "identical")  # an agreed "unchanged" is a clean refusal
        self.refused({"ok": True}, "unusable result")

    def test_hash_must_match_the_agreed_text(self):
        self.refused(self.good(hash="00" * 32), "mismatching text hash")
        self.refused(self.good(hash=None), "mismatching text hash")

    def test_plan_is_recomputed_by_the_contract(self):
        self.refused(self.good(kind="format_only"), "mismatching plan")  # the text really is a judged change

    def test_rating_must_be_on_the_scale(self):
        for bad in ("UNCLEAR", "BASELINE", "worse", "", None, 5):
            with self.subTest(bad=bad):
                self.refused(self.good(effect=bad), "invalid rating")

    def test_failures_are_reported_as_external(self):
        self.refused({"ok": False, "reason": "fetch"}, "could not be fetched")

    def test_a_forged_quote_is_neutralised_and_the_verdict_downgraded(self):
        self.forge(self.good(quote="a quote that is nowhere in the redline"))
        rid = int(self.c.append_revision(1, "https://example.com/terms"))
        r = self.rev(rid)
        self.assertEqual((r.effect, r.quote, r.raw_effect), ("UNCLEAR", "", "WORSE"))

    def test_a_forged_summary_is_cleaned_and_capped(self):
        self.forge(self.good(summary="x\x00y" + "z" * 2000))
        r = self.rev(int(self.c.append_revision(1, "https://example.com/terms")))
        self.assertEqual(len(r.summary), self.m.MAX_SUMMARY_CHARS)
        self.assertTrue(r.summary.startswith("xyz"))

    def test_the_honest_form_of_the_same_result_is_accepted(self):
        self.forge(self.good())
        r = self.rev(int(self.c.append_revision(1, "https://example.com/terms")))
        self.assertEqual((r.effect, r.quote, r.severity), ("WORSE", QUOTE_DATA, "HIGH"))


class ForgedAppeal(Forged):
    def setUp(self):
        super().setUp()
        self.register()
        self.rid = self.append(1, V3_ADVERSE, "WORSE", QUOTE_DATA)

    def test_invalid_rating_is_refused(self):
        for bad in ("UNCLEAR", "BASELINE", None, "", 3):
            with self.subTest(bad=bad):
                self.forge({"ok": True, "effect": bad, "summary": "s", "quote": ""})
                with self.revert("invalid rating"):
                    self.c.appeal_revision(self.rid)
                self.assertFalse(self.rev(self.rid).appealed)

    def test_a_failed_round_is_refused(self):
        self.forge({"ok": False, "reason": "model"})
        with self.revert("no usable verdict"):
            self.c.appeal_revision(self.rid)
        self.assertFalse(self.rev(self.rid).appealed)

    def test_a_forged_quote_is_neutralised(self):
        self.forge({"ok": True, "effect": "WORSE", "summary": "s", "quote": "nothing like this was ever written"})
        self.c.appeal_revision(self.rid)
        r = self.rev(self.rid)
        self.assertEqual((r.appeal_outcome, r.effect, r.quote, r.raw_effect), ("REVISED", "UNCLEAR", "", "WORSE"))


if __name__ == "__main__":
    unittest.main()
