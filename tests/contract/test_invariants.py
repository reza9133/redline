"""Randomised operation sequences. After EVERY operation (successful, reverted, or
without consensus) all counters, hashes and flags are recomputed from the stored
records alone and compared with what the contract reports."""
import functools
import json
import random
import unittest

from base import (ConsensusFailure, RedlineCase, Revert, UNRELATED, V1, V1_CAPS, V2_COSMETIC, V3_ADVERSE, V5_INJECTION,
                  clauses)

POOL = [V1, V1_CAPS, V2_COSMETIC, V3_ADVERSE, V5_INJECTION, UNRELATED, clauses("Alpha", 8), clauses("Beta", 8),
        V1 + " Extra plain sentence number one here.", V3_ADVERSE + " We may suspend accounts."]
EFFECTS = ["MUCH_BETTER", "BETTER", "NEUTRAL", "WORSE", "MUCH_WORSE"]
QUOTES = ["", "", "advertising partners", "binding arbitration", "do not sell your data", "plain English edition",
          "not a real quote anywhere", "Brand new", "terminate your account"]
ALL_EFFECTS = ("MUCH_BETTER", "BETTER", "NEUTRAL", "WORSE", "MUCH_WORSE", "UNCLEAR")


class Invariants(RedlineCase):
    def all_docs(self):
        out, off = [], 0
        while True:
            page = self.c.list_documents(off, 50)
            out += page
            if len(page) < 50:
                return out
            off += 50

    def revs_of(self, doc_id):
        out, off = [], 0
        while True:
            page = self.c.list_revisions(doc_id, off, 50)
            out += page
            if len(page) < 50:
                return out
            off += 50

    def check(self, where):
        m = self.m
        digest = functools.lru_cache(maxsize=None)(m._digest)  # pure-Python keccak is slow; texts repeat a lot
        chain = functools.lru_cache(maxsize=None)(m._chain)
        docs = self.all_docs()
        s = self.stats()
        ctx = f"after {where}"
        self.assertEqual(s["total_documents"], len(docs), ctx)
        judged = []
        total = 0
        for d in docs:
            revs = self.revs_of(d.id)
            total += len(revs)
            self.assertEqual(d.revision_count, len(revs), ctx)
            self.assertEqual([r.seq for r in revs], list(range(len(revs))), ctx)
            self.assertTrue(all(r.doc_id == d.id for r in revs), ctx)
            self.assertEqual(self.c.find_document(d.url), d.id, ctx)
            self.assertIn(d.id, self.c.list_owner_documents(d.owner.as_hex), ctx)
            head, prev_text = "", None
            for r in revs:
                text = self.c.get_revision_text(r.id)
                self.assertEqual(digest(text), r.text_hash, ctx)
                self.assertEqual(len(text), r.text_len, ctx)
                head = chain(head, r.text_hash)
                self.assertEqual(r.chain_hash, head, ctx)
                red = json.loads(self.c.get_redline(r.id))
                if r.seq == 0:
                    self.assertEqual((r.effect, r.severity, r.judged), ("BASELINE", "NONE", False), ctx)
                    self.assertEqual((red["added"], red["removed"]), ([], []), ctx)
                else:
                    plan = m._plan(prev_text, text, True)
                    self.assertIn(plan["kind"], ("judge", "format_only"), ctx)  # "unchanged" can never be stored
                    self.assertEqual((r.added_units, r.removed_units), (len(plan["added"]), len(plan["removed"])), ctx)
                    self.assertEqual((r.added_chars, r.removed_chars), (plan["added_chars"], plan["removed_chars"]), ctx)
                    self.assertEqual((r.retained_bp, r.churn_bp, r.rewrite), (plan["retained_bp"], plan["churn_bp"], plan["rewrite"]), ctx)
                    self.assertEqual(r.topics, ",".join(plan["topics"]), ctx)
                    self.assertEqual(r.injection_flagged, m._injection_flagged(plan["removed"] + plan["added"]), ctx)
                    self.assertEqual(red["added"], plan["shown_added"], ctx)
                    self.assertEqual(red["removed"], plan["shown_removed"], ctx)
                    self.assertEqual(r.judged, plan["kind"] == "judge", ctx)
                    # severity / silent are always the deterministic function of the stored facts
                    sev, silent = m._severity(r.effect, plan["topics"], plan["churn_bp"], plan["rewrite"])
                    self.assertEqual((r.severity, r.silent_flag), (sev, silent), ctx)
                    self.assertIn(r.effect, ALL_EFFECTS, ctx)
                    if r.effect not in ("NEUTRAL", "UNCLEAR"):
                        self.assertTrue(m._quote_ok(r.quote, plan["shown_added"], plan["shown_removed"]), ctx)  # no quote, no verdict
                    if r.effect == "UNCLEAR":
                        self.assertEqual(r.quote, "", ctx)
                    if r.judged:
                        judged.append(r)
                    if r.appeal_outcome == "REVISED":
                        self.assertTrue(r.appealed and r.prev_effect and r.prev_effect != r.effect, ctx)
                    else:
                        self.assertEqual(r.prev_effect, "", ctx)
                    self.assertEqual(r.appealed, r.appeal_outcome != "", ctx)
                prev_text = text
            last = revs[-1]
            self.assertEqual((d.latest_rev_id, d.latest_hash, d.chain_head, d.latest_url, d.last_revision_at),
                             (last.id, last.text_hash, head, last.url, last.created_at), ctx)
            body = [r for r in revs if r.seq > 0]
            self.assertEqual(d.better, sum(r.effect in ("BETTER", "MUCH_BETTER") for r in body), ctx)
            self.assertEqual(d.worse, sum(r.effect in ("WORSE", "MUCH_WORSE") for r in body), ctx)
            self.assertEqual(d.neutral, sum(r.effect == "NEUTRAL" for r in body), ctx)
            self.assertEqual(d.unclear, sum(r.effect == "UNCLEAR" for r in body), ctx)
            self.assertEqual(d.high, sum(r.severity == "HIGH" for r in body), ctx)
            self.assertEqual(d.silent, sum(r.silent_flag for r in body), ctx)
            self.assertEqual(d.rewrites, sum(r.rewrite for r in body), ctx)
        allrevs = [r for d in docs for r in self.revs_of(d.id) if r.seq > 0]
        self.assertEqual(s["total_revisions"], total, ctx)
        for key, label in (("much_better", "MUCH_BETTER"), ("better", "BETTER"), ("neutral", "NEUTRAL"),
                           ("worse", "WORSE"), ("much_worse", "MUCH_WORSE"), ("unclear", "UNCLEAR")):
            self.assertEqual(s[key], sum(r.effect == label for r in allrevs), f"{ctx}: {key}")
        for key, label in (("severity_low", "LOW"), ("severity_medium", "MEDIUM"), ("severity_high", "HIGH")):
            self.assertEqual(s[key], sum(r.severity == label for r in allrevs), f"{ctx}: {key}")
        self.assertEqual(sum(s[k] for k in ("much_better", "better", "neutral", "worse", "much_worse", "unclear")), len(allrevs), ctx)
        self.assertEqual(s["silent_flags"], sum(r.silent_flag for r in allrevs), ctx)
        self.assertEqual(s["rewrites"], sum(r.rewrite for r in allrevs), ctx)
        self.assertEqual(s["format_only"], sum(not r.judged for r in allrevs), ctx)
        self.assertEqual(s["appeals"], sum(r.appealed for r in allrevs), ctx)
        self.assertEqual(s["appeals_revised"], sum(r.appeal_outcome == "REVISED" for r in allrevs), ctx)

    def run_sequence(self, seed, steps):
        rng = random.Random(seed)
        owners = [self.alice, self.bob, self.carol]
        n_docs = 0
        counts = {"ok": 0, "revert": 0, "noconsensus": 0}
        for step in range(steps):
            op = rng.choice(["register", "append", "append", "append", "appeal", "wait", "bad"])
            label = f"seed {seed} step {step} op {op}"
            snapshot = (self.stats(), len(self.chain.events))
            try:
                if op == "register" and n_docs < 12:
                    self.page(rng.choice(POOL))
                    self.c.register_document(f"Doc {step}", "Pub", rng.choice(["TERMS", "PRIVACY"]),
                                             f"https://example.com/s{seed}/d{step}", sender=rng.choice(owners))
                    n_docs += 1
                elif op == "append" and n_docs:
                    doc = rng.randint(1, n_docs)
                    d = self.doc(doc)
                    self.page(rng.choice(POOL))
                    mode = rng.random()
                    if mode < 0.08:
                        self.chain.llm.fn(lambda p: RuntimeError("down"))
                    else:
                        self.llm(rng.choice(EFFECTS), "s", rng.choice(QUOTES))
                    self.chain.advance(rng.choice([0, 30, 61, 120]))
                    self.c.append_revision(doc, d.latest_url, sender=rng.choice(owners))
                elif op == "appeal" and n_docs:
                    total = self.stats()["total_revisions"]
                    self.llm(rng.choice(EFFECTS), "s", rng.choice(QUOTES))
                    self.c.appeal_revision(rng.randint(1, total), sender=rng.choice(owners))
                elif op == "wait":
                    self.chain.advance(rng.randint(1, 200))
                elif op == "bad":
                    self.c.append_revision(rng.randint(1, 5), rng.choice(["http://x.com/a", "https://10.0.0.1/a"]))
                else:
                    continue
                counts["ok"] += 1
            except Revert:
                counts["revert"] += 1
                self.assertEqual((self.stats(), len(self.chain.events)), snapshot, f"{label}: a revert must change nothing")
            except ConsensusFailure:
                counts["noconsensus"] += 1
                self.assertEqual((self.stats(), len(self.chain.events)), snapshot, f"{label}: no consensus must change nothing")
            self.check(label)
        return counts

    def test_random_sequences(self):
        totals = {"ok": 0, "revert": 0, "noconsensus": 0}
        for seed in range(12):
            self.setUp()  # a fresh chain per seed
            for k, v in self.run_sequence(seed, 70).items():
                totals[k] += v
        # the run must actually exercise every outcome, or it proves little
        self.assertGreater(totals["ok"], 300)
        self.assertGreater(totals["revert"], 60)
        self.assertGreater(totals["noconsensus"], 5)


if __name__ == "__main__":
    unittest.main()
