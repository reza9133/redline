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

    # ---- exact agreement on the stored verdict ---------------------------------
    def test_only_the_identical_label_is_accepted(self):
        """No tolerance band: the leader says WORSE, so only a validator that also says WORSE agrees."""
        for mine, ok in (("WORSE", True), ("MUCH_WORSE", False), ("NEUTRAL", False), ("BETTER", False), ("MUCH_BETTER", False)):
            with self.subTest(mine=mine):
                self.assertEqual(self.verdict(effect=mine), ok)

    def test_every_pair_of_labels_agrees_only_with_itself(self):
        for leader_label in self.m._SCALE:
            leader = dict(self.leader, effect=leader_label)
            for mine in self.m._SCALE:
                with self.subTest(leader=leader_label, mine=mine):
                    self.assertEqual(self.verdict(leader, effect=mine), leader_label == mine)

    def test_a_neutral_validator_cannot_vouch_for_a_better_or_a_worse_leader(self):
        """The rejection scenario: NEUTRAL used to accept BETTER and WORSE, and the leader's exact
        label drove the recorded effect, severity and counters."""
        for leader_label in ("BETTER", "WORSE"):
            with self.subTest(leader=leader_label):
                leader = dict(self.leader, effect=leader_label)
                self.assertFalse(self.verdict(leader, effect="NEUTRAL"))

    def test_a_neutral_leader_cannot_be_pushed_through_against_a_clear_verdict(self):
        leader = dict(self.leader, effect="NEUTRAL", quote="")
        self.assertFalse(self.verdict(leader, effect="WORSE"))
        self.assertFalse(self.verdict(leader, effect="MUCH_WORSE"))
        self.assertTrue(self.verdict(leader, effect="NEUTRAL"))

    def test_the_recorded_effect_is_exactly_the_label_the_round_agreed_on(self):
        """Full rounds (leader + replayed validator): the stored effect, severity and counters follow the
        agreed label one to one, so a neighbouring label can never end up recorded instead."""
        for label in self.m._SCALE:
            with self.subTest(label=label):
                url = "https://example.com/doc-" + label.lower()
                doc_id = self.register(url=url)
                rid = self.append(doc_id, V3_ADVERSE, label, QUOTE_DATA, url=url)
                rev = self.rev(rid)
                self.assertEqual((rev.effect, rev.raw_effect), (label, label))
                group = self.m._effect_group(label)
                self.assertEqual(int(getattr(self.doc(doc_id), group)), 1)

    def test_a_full_round_with_a_disagreeing_validator_is_not_recorded(self):
        """Leader BETTER, validator NEUTRAL (and the other way round): no consensus, nothing stored."""
        for leader_label, validator_label in (("BETTER", "NEUTRAL"), ("WORSE", "NEUTRAL"), ("NEUTRAL", "WORSE"), ("WORSE", "MUCH_WORSE")):
            with self.subTest(leader=leader_label, validator=validator_label):
                url = "https://example.com/doc-%s-%s" % (leader_label.lower(), validator_label.lower())
                doc_id = self.register(url=url)
                self.page(V3_ADVERSE)
                self.chain.advance(61)
                answers = iter((leader_label, validator_label))

                def split(prompt, answers=answers):
                    label = next(answers)
                    return '{"effect": "%s", "summary": "s", "quote": "advertising partners"}' % label

                self.chain.llm.fn(split)
                with self.assertRaises(ConsensusFailure):
                    self.c.append_revision(doc_id, url)
                self.assertEqual(self.doc(doc_id).revision_count, 1)

    # ---- the "no quote, no verdict" gate is part of the agreed outcome ------------
    def test_a_leader_without_a_grounded_quote_is_only_accepted_if_the_validator_has_none_either(self):
        """The leader's label is stored as UNCLEAR when it has no grounded quote. A validator that
        found a grounded quote of its own would have stored the real label, so it must reject."""
        leader = dict(self.leader, quote="")
        self.page(V3_ADVERSE)
        self.llm("WORSE", "s", "")  # I have no grounded quote either: both sides are UNCLEAR
        self.assertTrue(self.chain.run_validator(leader))
        self.llm("WORSE", "s", QUOTE_DATA)  # I do have one: my verdict is WORSE, the leader's would be UNCLEAR
        self.assertFalse(self.chain.run_validator(leader))

    def test_two_unclear_outcomes_still_need_the_same_underlying_label(self):
        """Both sides lack a grounded quote, so both would be recorded as UNCLEAR, but the raw label is
        stored too (and quoted in the summary): WORSE and BETTER must not vouch for each other."""
        self.page(V3_ADVERSE)
        for leader_label in ("MUCH_BETTER", "BETTER", "WORSE", "MUCH_WORSE"):
            leader = dict(self.leader, effect=leader_label, quote="")
            for mine in self.m._SCALE:
                if mine == "NEUTRAL":
                    continue  # NEUTRAL needs no quote, it would be recorded as NEUTRAL, not UNCLEAR
                with self.subTest(leader=leader_label, mine=mine):
                    self.llm(mine, "s", "")
                    self.assertEqual(self.chain.run_validator(leader), leader_label == mine)

    def test_a_validator_may_rely_on_a_verified_leader_quote_it_did_not_find_itself(self):
        self.page(V3_ADVERSE)
        self.llm("WORSE", "s", "")  # the model gave me no quote, the leader's is verified in Python
        self.assertTrue(self.chain.run_validator(self.leader))

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
        fabricated a NEUTRAL must not pass; the round has to be retried."""
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
