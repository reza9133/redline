#!/usr/bin/env python3
"""Does the LIVE script itself catch broken contracts?

Feeds deliberately broken copies of contracts/redline.py to e2e/live-e2e.mjs through the offline
rehearsal and expects the script to report at least one FAILED step for each of them.
A live script that stays green against a broken contract is worthless.

    python3 scripts/rehearsal_mutants.py            # the default set (about 5 minutes)
    python3 scripts/rehearsal_mutants.py -k chain   # only ids containing "chain"
"""
import argparse
import concurrent.futures
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mc", ROOT / "scripts" / "mutation_check.py")
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)
BY_ID = {m[0]: m for m in mc.M}

# (mutation id from mutation_check.py, fake-model mode, a step that must FAIL)
CASES = [
    ("chain-formula", "truthful", None),
    ("chain-head-not-updated", "truthful", None),
    ("acct-high", "truthful", "C02"),
    ("acct-worse", "truthful", "C02"),
    ("appeal-flag-not-set", "truthful", None),
    ("appeal-old-not-removed", "flipping", "C02"),
    ("appeal-new-not-added", "flipping", "C02"),
    ("appeal-effect", "flipping", None),
    ("cooldown-removed", "truthful", "C01"),
    ("site-rule-removed", "truthful", "P01"),
    ("plan-blocked-removed", "truthful", "P02"),
    ("plan-format-only-removed", "truthful", "F01"),
    ("unchanged-not-refused", "truthful", "I01"),
    ("reg-duplicate-check", "truthful", "H05"),
    ("reg-fingerprint-not-stored", "truthful", None),
    ("reg-min-text-removed", "truthful", "G01"),
    ("reg-url-check", "truthful", None),
    ("sev-silent-guard-off", "obedient", "N01"),
    ("sev-much-worse", "truthful", None),
    ("sev-rewrite-floor", "truthful", "P03"),
    ("finalize-no-quote-rule", "lazy", None),
    ("injection-never-flagged", "truthful", "N01"),
    ("rev-topics-not-stored", "truthful", None),
    ("rewrite-doc-counter", "truthful", None),
    ("format-only-counter", "truthful", None),
    ("rev-seq-wrong", "truthful", None),
    ("view-find-canonical", "truthful", "H06"),
    ("canon-no-lower", "truthful", "H05"),
]


def run(case):
    mid, mode, must_fail = case
    _, find, repl, nth = BY_ID[mid]
    mutated = mc.apply(mc.SOURCE.read_text(), find, repl, nth)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mutant.py"
        path.write_text(mutated)
        env = dict(os.environ, REHEARSAL_SOURCE=str(path), REHEARSAL_MODEL=mode, E2E_REPORT_DIR=str(Path(tmp) / "reports"))
        try:
            proc = subprocess.run(["node", "--import", "./tests/rehearsal/register.mjs", "e2e/live-e2e.mjs"],
                                  cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=420)
        except subprocess.TimeoutExpired:
            return mid, "TIMEOUT", []
        report = Path(tmp) / "reports" / "e2e-latest.json"
        steps = []
        if report.exists():
            steps = sorted(x["id"] for x in json.loads(report.read_text())["steps"] if x["status"] == "FAIL")
        else:
            steps = ["XX"]  # no report at all: the script died
    return mid, ("CAUGHT" if steps else "MISSED"), steps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="")
    ap.add_argument("-j", type=int, default=4)
    args = ap.parse_args()
    cases = [c for c in CASES if args.k in c[0]]
    missed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.j) as pool:
        for case, (mid, outcome, steps) in zip(cases, pool.map(run, cases)):
            expectation = ""
            if case[2] and outcome == "CAUGHT" and case[2] not in steps:
                expectation = f"  (expected {case[2]} among them!)"
                outcome = "MISSED"
            print(f"{outcome:7s} {mid:32s} model={case[1]:9s} failed steps: {','.join(steps) or '-'}{expectation}", flush=True)
            if outcome != "CAUGHT":
                missed.append(mid)
    print(f"\n{len(cases) - len(missed)}/{len(cases)} broken contracts were caught by the live script")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
