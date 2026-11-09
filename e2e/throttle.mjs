// Client-side rate limiter for the public Studionet RPC. It answers
// "Rate limit exceeded: 30 requests per minute" and then rejects every call, which would make a
// long end-to-end run fail at a random step.
//
// Import this module FIRST (before genlayer-js / viem). It wraps globalThis.fetch, the one
// transport that every RPC call, receipt poll and read goes through, so no call site needs to
// know about it:
//   * a sliding window keeps the run under the per-minute budget;
//   * an answer that is still "rate limited" is retried after a pause instead of failing a step;
//   * GitHub hosts (fixtures) are exempt: they have their own, much higher limits.
//
//   E2E_RPC_PER_MINUTE   max RPC requests per window (default 22; the server allows 30)
//   E2E_RPC_WINDOW_MS    window length in ms        (default 60000)
//   E2E_RPC_PENALTY_MS   base pause after a limited answer (default 12000)
const BUDGET = Math.max(1, Number(process.env.E2E_RPC_PER_MINUTE) || 22);
const WINDOW = Number(process.env.E2E_RPC_WINDOW_MS) || 60_000;
const PENALTY = Number(process.env.E2E_RPC_PENALTY_MS) || 12_000;
const MAX_RETRIES = 6;
const EXEMPT = /(^|\.)(github\.com|githubusercontent\.com)$/i;

const realFetch = globalThis.fetch;
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const sent = []; // timestamps of requests inside the current window
let line = Promise.resolve(); // serialises slot hand-out so concurrent callers queue fairly

function acquireSlot() {
  const turn = line.then(async () => {
    for (;;) {
      const now = Date.now();
      while (sent.length && now - sent[0] >= WINDOW) sent.shift();
      if (sent.length < BUDGET) {
        sent.push(now);
        return;
      }
      await wait(sent[0] + WINDOW - now + 25);
    }
  });
  line = turn.catch(() => {});
  return turn;
}

function hostOf(input) {
  try {
    return new URL(typeof input === "string" || input instanceof URL ? String(input) : input.url).hostname;
  } catch {
    return "";
  }
}

globalThis.fetch = async function throttledFetch(input, init) {
  if (EXEMPT.test(hostOf(input))) return realFetch(input, init);
  // A Request object's body can be consumed once, so only string/URL inputs are safe to replay.
  const replayable = typeof input === "string" || input instanceof URL;
  for (let attempt = 0; ; attempt++) {
    await acquireSlot();
    const res = await realFetch(input, init);
    let limited = res.status === 429;
    if (!limited && res.ok) {
      try {
        limited = /rate limit exceeded/i.test((await res.clone().text()).slice(0, 400));
      } catch {
        /* unreadable body: assume it is fine */
      }
    }
    if (!limited || !replayable || attempt >= MAX_RETRIES) return res;
    const pause = PENALTY + attempt * 4000;
    process.stdout.write(`\n    (RPC rate limit hit; pausing ${Math.round(pause / 1000)}s) `);
    await wait(pause);
  }
};
