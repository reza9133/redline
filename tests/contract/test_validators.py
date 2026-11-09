"""Consensus logic: what a validator accepts, given what the leader claims.

The harness runs the leader once and captures the validator closure; swapping the
web / LLM mocks and calling `run_validator` models a validator that lives in a
different world than the leader (another page, a disagreeing model, ...)."""
import unittest

from base import (ConsensusFailure, QUOTE_ARBITRATION, QUOTE_DATA, RedlineCase, Revert, URL, UNRELATED, V1, V1_CAPS, V3_ADVERSE)


class AppendValidator(RedlineCase):
    def setUp(self):
        super().setUp()
        self.register()
        self.append(1, V3_ADVERSE, "WORSE", QUOTE_DATA)
        self.leader = self.chain.leader_result()

    def verdict(self, leader=None, *, effect="WORSE", page=None, raw=None):
        self.page(V3_ADVERSE if page is None else page)
        self.llm(effect, "s", "")
        return self.chain.run_validator(leader, raw=raw)

    # ---- the honest case ---------------------------------------------------
    def test_honest_leader_is_accepted(self):
        self.assertEqual(self.leader["kind"], "judge")
        self.assertEqual((self.leader["effect"], self.leader["quote"]), ("WORSE", QUOTE_DATA))
        self.assertTrue(self.verdict())

    def test_summary_wording_is_not_part_of_consensus(self):
        self.page(V3_ADVERSE)
        self.chain.llm.set("WORSE", "Completely different words.", "something else entirely")
        self.assertTrue(self.chain.run_validator())  # the LEADER's quote is what is checked, not mine

    # ---- bounded disagreement ------------------------------------------------
    def test_one_step_apart_is_accepted_two_steps_is_not(self):
        for mine, ok in (("WORSE", True), ("MUCH_WORSE", True), ("NEUTRAL", True), ("BETTER", False), ("MUCH_BETTER", False)):
            with self.subTest(mine=mine):
                self.assertEqual(self.verdict(effect=mine), ok)

    def test_extremes_never_agree(self):
        leader = dict(self.leader, effect="MUCH_WORSE")
        self.assertTrue(self.verdict(leader, effect="WORSE"))
        for mine in ("NEUTRAL", "BETTER", "MUCH_BETTER"):
            self.assertFalse(self.verdict(leader, effect=mine), mine)
        leader = dict(self.leader, effect="BETTER", quote="")
        self.assertTrue(self.verdict(leader, effect="NEUTRAL"))
        self.assertFalse(self.verdict(leader, effect="WORSE"))  # opposite sides, two steps apart

    def test_neutral_leader_cannot_be_pushed_through_against_a_clear_verdict(self):
        leader = dict(self.leader, effect="NEUTRAL", quote="")
        self.assertTrue(self.verdict(leader, effect="WORSE"))   # one step
        self.assertFalse(self.verdict(leader, effect="MUCH_WORSE"))

    # ---- a lying or sloppy leader ------------------------------------------------
    def test_rejects_when_validator_sees_a_different_page(self):
        self.assertFalse(self.verdict(page=V3_ADVERSE + " (edited)"))

    def test_rejects_forged_snapshot_or_hash(self):
        self.assertFalse(self.verdict(dict(self.leader, snapshot=V3_ADVERSE + "!")))
        self.assertFalse(self.verdict(dict(self.leader, hash="00" * 32)))
        forged = dict(self.leader)
        del forged["snapshot"]
        self.assertFalse(self.verdict(forged))

    def test_rejects_a_forged_kind(self):
        for kind in ("unchanged", "blocked", "format_only", "nonsense", None):
            with self.subTest(kind=kind):
                self.assertFalse(self.verdict(dict(self.leader, kind=kind)))

    def test_rejects_invented_quotes(self):
        self.assertFalse(self.verdict(dict(self.leader, quote="We guarantee arbitration never happens")))
        self.assertFalse(self.verdict(dict(self.leader, quote="Our support team answers questions by email")))  # unchanged text
        self.assertFalse(self.verdict(dict(self.leader, quote="ads")))  # too short
        self.assertFalse(self.verdict(dict(self.leader, quote="advertising  partners")))  # not in stored form

    def test_a_quote_from_the_removed_side_is_fine(self):
        self.assertTrue(self.verdict(dict(self.leader, quote="do not sell your data")))

    def test_an_empty_quote_is_not_the_validators_problem(self):
        self.assertTrue(self.verdict(dict(self.leader, quote="")))  # the contract downgrades it to UNCLEAR

    def test_rejects_ratings_outside_the_scale(self):
        for bad in ("UNCLEAR", "BASELINE", "worse", "", None, 3, ["WORSE"]):
            with self.subTest(bad=bad):
                self.assertFalse(self.verdict(dict(self.leader, effect=bad)))

    def test_rejects_malformed_leader_results(self):
        for bad in ("nope", 7, [], {}, {"ok": True}, {"ok": True, "kind": "judge"}, {"ok": "yes"}):
            with self.subTest(bad=bad):
                self.assertFalse(self.verdict(bad))
        self.assertFalse(self.verdict(raw=self.m.gl.vm.UserError("leader crashed")))

    # ---- model trouble on the validator side ------------------------------------------------
    def test_a_validator_without_a_usable_model_answer_rejects(self):
        self.page(V3_ADVERSE)
        for handler in (lambda p: RuntimeError("down"), lambda p: "garbage", lambda p: '{"effect": "??"}'):
            self.chain.llm.fn(handler)
            self.assertFalse(self.chain.run_validator())

    # ---- fetch failures -------------------------------------------------------------------------
    def test_reported_fetch_failure_is_only_believed_if_the_validator_cannot_read_it_either(self):
        failure = {"ok": False, "reason": "fetch"}
        self.chain.web.clear()
        self.assertTrue(self.chain.run_validator(failure))
        self.page(V3_ADVERSE)
        self.assertFalse(self.chain.run_validator(failure))

    def test_a_model_failure_is_never_rubber_stamped(self):
        for reason in ("model", "", None, "other"):
            for world in ("web", "noweb"):
                self.chain.web.clear()
                if world == "web":
                    self.page(V3_ADVERSE)
                self.assertFalse(self.chain.run_validator({"ok": False, "reason": reason}), (reason, world))
        self.assertFalse(self.chain.run_validator({"ok": False}))

    def test_cheap_rejections_do_not_spend_a_model_call(self):
        self.page(V3_ADVERSE)
        self.llm("WORSE", "s", "")
        calls = len(self.chain.llm.calls)
        for bad in (dict(self.leader, effect="UNCLEAR"), dict(self.leader, effect=None), dict(self.leader, hash="00" * 32),
                    dict(self.leader, snapshot="x"), dict(self.leader, kind="unchanged"), dict(self.leader, quote="invented words here"),
                    {"ok": False, "reason": "model"}, "junk"):
            self.assertFalse(self.chain.run_validator(bad))
        self.assertEqual(len(self.chain.llm.calls), calls)
        self.assertTrue(self.chain.run_validator())  # the honest result does consult the model
        self.assertEqual(len(self.chain.llm.calls), calls + 1)

    def test_a_leader_whose_model_failed_cannot_get_through_on_the_validators_answer(self):
        """The leader's model is down, the validator's model works and says WORSE. A leader that
        fabricated a NEUTRAL (one step away) must not pass; the round has to be retried."""
        self.register(url="https://example.com/second")
        self.page(V3_ADVERSE)
        calls = {"n": 0}

        def flaky(prompt):
            calls["n"] += 1
            if calls["n"] == 1:
                return RuntimeError("the leader's model is down")
            return '{"effect": "WORSE", "summary": "s", "quote": "advertising partners"}'

        self.chain.llm.fn(flaky)
        self.chain.advance(61)
        with self.assertRaises(ConsensusFailure):
            self.c.append_revision(2, "https://example.com/second")
        self.assertEqual(self.chain.leader_result(), {"ok": False, "reason": "model"})
        self.assertEqual(self.doc(2).revision_count, 1)

    def test_a_validator_that_cannot_read_the_page_rejects_a_successful_leader(self):
        self.chain.web.clear()
        self.assertFalse(self.chain.run_validator())


class OtherKinds(RedlineCase):
    def setUp(self):
        super().setUp()
        self.register()
        self.chain.advance(61)

    def test_unchanged(self):
        self.page(V1)
        with self.assertRaises(Revert):
            self.c.append_revision(1, URL)
        leader = self.chain.leader_result()
        self.assertEqual(leader["kind"], "unchanged")
        self.assertTrue(self.chain.run_validator())
        self.page(V3_ADVERSE)  # the validator sees a real change: the leader tried to suppress it
        self.assertFalse(self.chain.run_validator())
        self.page(V1)
        self.assertFalse(self.chain.run_validator(dict(leader, hash="ff" * 32)))

    def test_a_leader_cannot_censor_a_real_change_by_claiming_it_is_unchanged(self):
        self.page(V3_ADVERSE)
        self.llm("WORSE", "s", QUOTE_DATA)
        self.c.append_revision(1, URL)
        liar = {"ok": True, "kind": "unchanged", "hash": self.m._digest(V3_ADVERSE)}
        self.assertFalse(self.chain.run_validator(liar))

    def test_blocked(self):
        self.page(UNRELATED)
        with self.assertRaises(Revert):
            self.c.append_revision(1, URL, sender=self.bob)
        leader = self.chain.leader_result()
        self.assertEqual(leader["kind"], "blocked")
        self.assertTrue(self.chain.run_validator())
        self.assertFalse(self.chain.run_validator(dict(leader, kind="judge")))
        self.page(V3_ADVERSE)
        self.assertFalse(self.chain.run_validator())

    def test_owner_flag_is_part_of_the_plan(self):
        self.page(UNRELATED)
        self.llm("NEUTRAL", "s", "")
        self.c.append_revision(1, URL, sender=self.alice)  # owner: judged, not blocked
        self.assertEqual(self.chain.leader_result()["kind"], "judge")
        self.assertFalse(self.chain.run_validator(dict(self.chain.leader_result(), kind="blocked")))

    def test_format_only(self):
        self.page(V1_CAPS)
        self.chain.llm.clear()
        self.c.append_revision(1, URL)
        leader = self.chain.leader_result()
        self.assertEqual(leader["kind"], "format_only")
        self.assertNotIn("effect", leader)
        self.assertTrue(self.chain.run_validator())
        self.assertFalse(self.chain.run_validator(dict(leader, snapshot=V1_CAPS + "!")))
        self.assertFalse(self.chain.run_validator(dict(leader, kind="judge", effect="NEUTRAL", quote="")))
        self.page(V3_ADVERSE)
        self.assertFalse(self.chain.run_validator())


class AppendValidatorNeverRunsAModelForPlanOnlyKinds(RedlineCase):
    def test_no_model_for_unchanged_blocked_or_format_only(self):
        self.register()
        self.chain.advance(61)
        self.chain.llm.clear()
        for body, sender, expect in ((V1, None, "unchanged"), (UNRELATED, self.bob, "blocked"), (V1_CAPS, None, "format_only")):
            self.page(body)
            try:
                self.c.append_revision(1, URL, sender=sender)
            except Revert:
                pass
            self.assertEqual(self.chain.leader_result()["kind"], expect)
            self.assertTrue(self.chain.run_validator())
            if expect == "format_only":
                self.chain.advance(61)
        self.assertEqual(self.chain.llm.calls, [])


if __name__ == "__main__":
    unittest.main()
