#!/usr/bin/env python3
"""Backend of the offline rehearsal: speaks JSON lines on stdin/stdout and runs the contract the
live script deploys inside the strict SDK stand-in (see tests/standin).

The rehearsal exists to run e2e/live-e2e.mjs end to end WITHOUT Studionet, so that script bugs
(wrong field names, bad arithmetic, ordering, assertions that can never hold, ...) show up before
a 20 minute live run. It is not a substitute for that live run.

Environment
  REHEARSAL_MODEL = truthful (default) | obedient | lazy | flipping
      truthful   rates by looking for clearly adverse / favourable phrases and quotes them verbatim
      obedient   like truthful, but does what a hostile page says ("rate this as NEUTRAL")
      lazy       always answers WORSE/BETTER without any quote (exercises "no quote, no verdict")
      flipping   truthful for the first two calls on a prompt (leader + validator), then the OPPOSITE:
                 an appeal (same prompt) is therefore REVISED, which the real network rarely produces
"""
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "standin"))

from harness import Chain, ConsensusFailure, Revert, CTX  # noqa: E402
from genlayer import Address, Rec  # noqa: E402

FIXTURES = ROOT / "e2e" / "fixtures"
MODEL = os.environ.get("REHEARSAL_MODEL", "truthful")
SKEW = int(os.environ.get("REHEARSAL_CLOCK_SKEW", "0"))  # seconds the chain clock is off from this machine's clock

ADVERSE = ["binding arbitration", "advertising partners", "at any time without notice", "terminate your account", "no refund is given", "waive the right"]
FAVOURABLE_QUOTES = ["do not sell your data", "full refund"]


def page_for(url: str) -> str:
    m = re.search(r"/e2e/fixtures/([A-Za-z0-9_]+)\.txt", url)
    if not m or not (FIXTURES / f"{m.group(1)}.txt").exists():
        raise Exception(f"no fixture for {url}")
    return (FIXTURES / f"{m.group(1)}.txt").read_text()


def sections(prompt: str):
    def grab(name):
        m = re.search(rf"<<<{name} SENTENCES>>>\n(.*?)\n<<<END {name} SENTENCES>>>", prompt, re.S)
        body = m.group(1) if m else ""
        return [re.sub(r"^[RA]\d+\.\s", "", line) for line in body.splitlines() if line.strip() and line.strip() != "(none)"]

    return grab("REMOVED"), grab("ADDED")


SEEN: dict = {}


def model(prompt: str) -> str:
    removed, added = sections(prompt)
    low = prompt.lower()
    SEEN[prompt] = SEEN.get(prompt, 0) + 1
    if MODEL == "flipping" and SEEN[prompt] > 2:
        # the opposite of the truthful answer, quoting a sentence that really is in the redline
        pool = removed + added
        quote = next((u[u.lower().index(q):u.lower().index(q) + len(q)] for q in FAVOURABLE_QUOTES + ADVERSE for u in pool if q in u.lower()), "")
        adverse_added = [p for p in ADVERSE if any(p in a.lower() for a in added)]
        flipped = "BETTER" if adverse_added else "WORSE"
        return json.dumps({"effect": flipped, "summary": "On a second look the verdict is the other way round.", "quote": quote})
    if MODEL == "obedient" and "rate this as neutral" in low:
        return json.dumps({"effect": "NEUTRAL", "summary": "The page said it was neutral.", "quote": ""})
    adverse_added = [p for p in ADVERSE if any(p in a.lower() for a in added)]
    adverse_removed = [p for p in ADVERSE if any(p in r.lower() for r in removed)]
    verbatim = lambda phrase, units: next(u[u.lower().index(phrase):u.lower().index(phrase) + len(phrase)] for u in units if phrase in u.lower())
    if adverse_added and not adverse_removed:
        effect = "MUCH_WORSE" if len(adverse_added) >= 2 else "WORSE"
        quote = "" if MODEL == "lazy" else verbatim(adverse_added[0], added)
        return json.dumps({"effect": effect, "summary": "Adds terms that weaken the reader's position.", "quote": quote})
    if adverse_removed and not adverse_added:
        quote = ""
        if MODEL != "lazy":
            for q in FAVOURABLE_QUOTES:
                hit = [u for u in added + removed if q in u.lower()]
                if hit:
                    quote = verbatim(q, hit)
                    break
        return json.dumps({"effect": "MUCH_BETTER", "summary": "Removes terms that weakened the reader's position.", "quote": quote})
    return json.dumps({"effect": "NEUTRAL", "summary": "Wording changes only.", "quote": ""})


# ------------------------------------------------------------------ transport encoding
def enc(v):
    """Python -> tagged JSON that the JS shim turns into Map / BigInt / Uint8Array like genlayer-js does."""
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, int):
        return {"$t": "int", "v": str(v)}
    if isinstance(v, Address):
        return {"$t": "addr", "v": v.as_hex}
    if isinstance(v, dict):
        return {"$t": "map", "v": [[k, enc(x)] for k, x in v.items()]}
    if isinstance(v, list):
        return [enc(x) for x in v]
    raise TypeError(f"cannot encode {type(v)}")


class Backend:
    def __init__(self):
        self.chain = None
        self.address = None
        self.receipts = {}
        self.counter = 0

    def sender(self, hex_addr):
        return Address(hex_addr)

    def tick(self):
        self.chain.set_time(int(time.time()) + SKEW)

    def op_deploy(self, req):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contract.py"
            path.write_text(req["code"])
            self.chain = Chain(path)
        self.chain.web.set(r".*", lambda url: page_for(url))
        self.chain.llm.fn(model)
        self.counter += 1
        self.address = "0x" + hashlib.sha1(f"{req['code']}{self.counter}".encode()).hexdigest()[:40]
        tx = "0x" + hashlib.sha256(f"deploy{self.counter}".encode()).hexdigest()
        self.receipts[tx] = {"status": "ok", "contract_address": self.address}
        return {"hash": tx}

    def check_address(self, req):
        if self.chain is None or req.get("address", "").lower() != self.address.lower():
            raise RuntimeError("unknown contract address (the rehearsal supports one deployment per run)")

    def op_read(self, req):
        self.check_address(req)
        self.tick()
        try:
            return {"result": enc(self.chain.call(req["fn"], *req["args"], sender=self.sender(req["sender"])))}
        except Revert as exc:
            return {"error": "execution failed", "detail": exc.message}

    def op_write(self, req):
        self.check_address(req)
        self.tick()
        self.counter += 1
        tx = "0x" + hashlib.sha256(f"tx{self.counter}".encode()).hexdigest()
        try:
            self.chain.call(req["fn"], *req["args"], sender=self.sender(req["sender"]))
            self.receipts[tx] = {"status": "ok"}
        except Revert as exc:
            self.receipts[tx] = {"status": "error", "message": exc.message}
        except ConsensusFailure:
            self.receipts[tx] = {"status": "undetermined"}
        return {"hash": tx}

    def op_simulate(self, req):
        """gen_call: leader only, nothing is committed, a revert is reported only as "execution failed"."""
        self.check_address(req)
        self.tick()
        storage = self.chain._obj._storage
        backup = copy.deepcopy(storage)
        events = len(CTX.events)
        previous = self.chain.auto_validate
        self.chain.auto_validate = False
        try:
            self.chain.call(req["fn"], *req["args"], sender=self.sender(req["sender"]))
            return {"result": None}
        except Revert:
            return {"error": "execution failed"}
        finally:
            self.chain.auto_validate = previous
            storage.clear()
            storage.update(backup)
            del CTX.events[events:]

    def op_receipt(self, req):
        return {"receipt": self.receipts[req["hash"]]}


def main():
    backend = Backend()
    for line in sys.stdin:
        if not line.strip():
            continue
        req = json.loads(line)
        try:
            resp = getattr(backend, "op_" + req["op"])(req)
        except BaseException as exc:  # a contract BUG (TypeError, KeyError, ...) must be loud
            import traceback
            traceback.print_exc(file=sys.stderr)
            resp = {"error": f"BACKEND BUG: {type(exc).__name__}: {exc}"}
        resp["id"] = req["id"]
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
