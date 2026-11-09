import json
import unittest

from base import (ConsensusFailure, QUOTE_DATA, RedlineCase, Revert, URL, UNRELATED, V1, V1_CAPS, V2_COSMETIC, V3_ADVERSE)


class Appeal(RedlineCase):
    def setUp(self):
        super().setUp()
        self.register()
        self.rid = self.append(1, V3_ADVERSE, "WORSE", QUOTE_DATA, "Original summary.")  # WORSE, HIGH (topics)

    def appeal(self, effect, quote="", summary="Appeal summary.", sender=None, rid=None):
        self.llm(effect, summary, quote)
        self.chain.advance(5)
        return self.c.appeal_revision(self.rid if rid is None else rid, sender=sender)

    # ---- confirmed ----------------------------------------------------------------
    def test_confirmed_appeal_changes_nothing_but_the_appeal_fields(self):
        before = self.rev(self.rid)
        stats = self.stats()
        self.appeal("WORSE", QUOTE_DATA, sender=self.bob)
        after = self.rev(self.rid)
        self.assertEqual((after.appealed, after.appeal_outcome, after.appeal_by, after.appeal_at),
                         (True, "CONFIRMED", self.bob, self.chain.now))
        for field in ("effect", "raw_effect", "severity", "silent_flag", "summary", "quote", "prev_effect", "text_hash", "chain_hash"):
            self.assertEqual(after[field], before[field], field)
        self.assertEqual(after.summary, "Original summary.")
        expected = dict(stats, appeals=1)
        self.assertEqual(self.stats(), expected)
        self.assertEqual(self.chain.events[-1], ("RevisionAppealed", (self.rid,), {"outcome": "CONFIRMED"}))

    def test_one_step_different_rating_is_still_a_different_verdict(self):
        self.appeal("MUCH_WORSE", QUOTE_DATA)
        r = self.rev(self.rid)
        self.assertEqual((r.appeal_outcome, r.effect, r.prev_effect, r.raw_effect), ("REVISED", "MUCH_WORSE", "WORSE", "MUCH_WORSE"))

    # ---- revised ------------------------------------------------------------------------
    def test_revised_appeal_moves_every_counter(self):
        self.appeal("BETTER", "do not sell your data", "On reflection this is fine.")
        r = self.rev(self.rid)
        self.assertEqual((r.effect, r.raw_effect, r.prev_effect, r.appeal_outcome), ("BETTER", "BETTER", "WORSE", "REVISED"))
        self.assertEqual((r.severity, r.silent_flag), ("MEDIUM", False))  # topics are the stored ones -> heavy
        self.assertEqual((r.summary, r.quote), ("On reflection this is fine.", "do not sell your data"))
        d = self.doc(1)
        self.assertEqual((d.worse, d.better, d.high, d.neutral, d.unclear), (0, 1, 0, 0, 0))
        s = self.stats()
        self.assertEqual((s["worse"], s["better"], s["severity_high"], s["severity_medium"]), (0, 1, 0, 1))
        self.assertEqual((s["appeals"], s["appeals_revised"]), (1, 1))
        self.assertEqual(s["total_revisions"], 2)

    def test_revised_to_unclear_when_the_appeal_has_no_quote(self):
        self.appeal("MUCH_WORSE", "")
        r = self.rev(self.rid)
        self.assertEqual((r.effect, r.raw_effect, r.prev_effect, r.quote), ("UNCLEAR", "MUCH_WORSE", "WORSE", ""))
        self.assertEqual((self.doc(1).worse, self.doc(1).unclear), (0, 1))
        self.assertEqual((self.stats()["worse"], self.stats()["unclear"], self.stats()["severity_medium"]), (0, 1, 1))

    def test_revised_into_the_silent_guard_and_back_out(self):
        self.appeal("NEUTRAL", "")
        r = self.rev(self.rid)
        self.assertEqual((r.effect, r.silent_flag, r.severity), ("NEUTRAL", True, "MEDIUM"))
        self.assertEqual((self.doc(1).silent, self.stats()["silent_flags"], self.doc(1).neutral), (1, 1, 1))
        # a second revision, silent from the start, appealed out of the guard
        self.register(url="https://example.com/b")
        rid2 = self.append(2, V3_ADVERSE, "NEUTRAL", "", url="https://example.com/b")
        self.assertTrue(self.rev(rid2).silent_flag)
        self.assertEqual(self.stats()["silent_flags"], 2)
        self.appeal("WORSE", QUOTE_DATA, rid=rid2)
        self.assertFalse(self.rev(rid2).silent_flag)
        self.assertEqual((self.stats()["silent_flags"], self.doc(2).silent), (1, 0))

    def test_the_appeal_never_touches_text_chain_or_document_head(self):
        d0, r0 = self.doc(1), self.rev(self.rid)
        self.appeal("BETTER", "do not sell your data")
        d1, r1 = self.doc(1), self.rev(self.rid)
        for f in ("chain_head", "latest_hash", "latest_rev_id", "revision_count", "last_revision_at", "latest_url"):
            self.assertEqual(d1[f], d0[f], f)
        for f in ("text_hash", "chain_hash", "retained_bp", "churn_bp", "topics", "added_units", "removed_units", "created_at"):
            self.assertEqual(r1[f], r0[f], f)
        self.assertEqual(self.c.get_revision_text(self.rid), V3_ADVERSE)

    def test_rewrite_flag_keeps_the_severity_floor(self):
        did = self.register(url="https://example.com/rw")
        self.page(UNRELATED)
        self.llm("NEUTRAL", "s", "")
        self.chain.advance(61)
        rid = int(self.c.append_revision(did, "https://example.com/rw", sender=self.alice))
        self.assertEqual((self.rev(rid).rewrite, self.rev(rid).severity), (True, "MEDIUM"))
        self.appeal("BETTER", "sourdough bakery blog", rid=rid)
        r = self.rev(rid)
        self.assertEqual((r.effect, r.rewrite, r.severity), ("BETTER", True, "MEDIUM"))

    def test_a_quiet_rewrite_keeps_its_severity_floor_through_an_appeal(self):
        old = " ".join(f"Old clause {i:02d} is written here." for i in range(10))
        new = " ".join(f"Old clause {i:02d} is really written here." for i in range(10))  # every sentence differs, few words do
        did = self.register(body=old, url="https://example.com/quiet")
        self.page(new)
        self.llm("NEUTRAL", "s", "")
        self.chain.advance(61)
        rid = int(self.c.append_revision(did, "https://example.com/quiet", sender=self.alice))
        r = self.rev(rid)
        self.assertEqual((r.rewrite, r.topics, r.silent_flag, r.severity), (True, "", False, "MEDIUM"))
        self.assertLess(r.churn_bp, self.m.HEAVY_CHURN_BP)
        self.appeal("BETTER", "is really written here", rid=rid)
        r = self.rev(rid)
        self.assertEqual((r.effect, r.rewrite, r.severity), ("BETTER", True, "MEDIUM"))  # the floor survives; a non-rewrite would be LOW

    # ---- the input is the stored redline ---------------------------------------------------------
    def test_appeal_needs_no_network_and_judges_the_stored_redline(self):
        calls = len(self.chain.web.calls)
        self.chain.web.clear()  # the page is gone; an appeal does not care
        self.appeal("WORSE", QUOTE_DATA)
        self.assertEqual(len(self.chain.web.calls), calls)
        prompt = self.chain.llm.calls[-1]
        stored = json.loads(self.c.get_redline(self.rid))
        for unit in stored["added"] + stored["removed"]:
            self.assertIn(unit, prompt)
        self.assertIn("DOCUMENT: Northwind ToS", prompt)

    def test_appeal_judges_exactly_what_the_first_round_judged(self):
        first_prompt = self.chain.llm.calls[0]
        self.appeal("WORSE", QUOTE_DATA)
        self.assertEqual(self.chain.llm.calls[-1], first_prompt)

    # ---- who / when ---------------------------------------------------------------------------------
    def test_anyone_may_appeal_but_only_once(self):
        self.appeal("WORSE", QUOTE_DATA, sender=self.carol)
        self.assertEqual(self.rev(self.rid).appeal_by, self.carol)
        for who in (self.carol, self.bob, self.alice):
            with self.revert("already been appealed"):
                self.appeal("WORSE", QUOTE_DATA, sender=who)
        self.assertEqual(self.stats()["appeals"], 1)

    def test_baseline_format_only_and_unknown_revisions_cannot_be_appealed(self):
        self.llm("WORSE", "s", QUOTE_DATA)
        with self.revert("baseline revision has no verdict"):
            self.c.appeal_revision(1)
        self.page(V1_CAPS)
        self.chain.advance(61)
        # V3 -> V1_CAPS is a real change, so craft a genuine format-only revision on another document
        self.register(url="https://example.com/fmt")
        self.page(V1_CAPS)
        self.chain.advance(61)
        fid = int(self.c.append_revision(2, "https://example.com/fmt"))
        self.assertFalse(self.rev(fid).judged)
        with self.revert("nothing to appeal"):
            self.c.appeal_revision(fid)
        with self.revert("revision not found"):
            self.c.appeal_revision(999)

    def test_appeal_after_the_revision_cap_or_cooldown_is_unaffected(self):
        # an appeal is not a revision: no cooldown, no cap
        self.llm("WORSE", "s", QUOTE_DATA)
        self.c.appeal_revision(self.rid)  # right away, same second as the append
        self.assertTrue(self.rev(self.rid).appealed)

    # ---- failures --------------------------------------------------------------------------------------
    def test_a_failed_appeal_changes_nothing_and_does_not_consume_the_right(self):
        before = (self.stats(), dict(self.rev(self.rid)), dict(self.doc(1)))
        for handler in (lambda p: RuntimeError("down"), lambda p: "garbage", lambda p: '{"effect": "??"}'):
            self.chain.llm.fn(handler)
            with self.assertRaises(ConsensusFailure):
                self.c.appeal_revision(self.rid)
        self.assertEqual((self.stats(), dict(self.rev(self.rid)), dict(self.doc(1))), before)
        self.appeal("WORSE", QUOTE_DATA)
        self.assertTrue(self.rev(self.rid).appealed)

    def test_a_leader_whose_model_failed_cannot_get_through_on_the_validators_answer(self):
        calls = {"n": 0}

        def flaky(prompt):
            calls["n"] += 1
            return RuntimeError("down") if calls["n"] == 1 else '{"effect": "WORSE", "summary": "s", "quote": "advertising partners"}'

        self.chain.llm.fn(flaky)
        with self.assertRaises(ConsensusFailure):
            self.c.appeal_revision(self.rid)
        self.assertEqual(self.chain.leader_result(), {"ok": False, "reason": "model"})
        self.assertFalse(self.rev(self.rid).appealed)

    def test_corrupt_stored_redline_is_reported_not_crashed(self):
        self.chain._obj._storage["rev_redlines"]._d[self.m.u256(self.rid)] = "not json"
        self.llm("WORSE", "s", QUOTE_DATA)
        with self.revert("stored redline is unreadable"):
            self.c.appeal_revision(self.rid)


class AppealValidator(RedlineCase):
    def setUp(self):
        super().setUp()
        self.register()
        self.rid = self.append(1, V3_ADVERSE, "WORSE", QUOTE_DATA)
        self.llm("WORSE", "s", QUOTE_DATA)
        self.c.appeal_revision(self.rid)
        self.leader = self.chain.leader_result()

    def check(self, leader=None, effect="WORSE", raw=None):
        self.llm(effect, "s", "")
        return self.chain.run_validator(leader, raw=raw)

    def test_honest_accepted(self):
        self.assertEqual(self.leader["ok"], True)
        self.assertTrue(self.check())

    def test_bounded_disagreement(self):
        for mine, ok in (("WORSE", True), ("MUCH_WORSE", True), ("NEUTRAL", True), ("BETTER", False), ("MUCH_BETTER", False)):
            with self.subTest(mine=mine):
                self.assertEqual(self.check(effect=mine), ok)

    def test_forged_quote_or_rating_rejected(self):
        self.assertFalse(self.check(dict(self.leader, quote="mandatory arbitration forever")))
        self.assertFalse(self.check(dict(self.leader, effect="UNCLEAR")))
        self.assertFalse(self.check(dict(self.leader, effect=None)))
        self.assertFalse(self.check(dict(self.leader, quote="Our support team answers questions by email")))  # not in the stored redline

    def test_failures_and_junk_rejected(self):
        for bad in ({"ok": False, "reason": "model"}, {"ok": False}, {}, "nope", 5, []):
            self.assertFalse(self.check(bad), bad)
        self.assertFalse(self.check(raw=self.m.gl.vm.UserError("x")))

    def test_validator_model_failure_rejects(self):
        self.chain.llm.fn(lambda p: RuntimeError("down"))
        self.assertFalse(self.chain.run_validator())


if __name__ == "__main__":
    unittest.main()
