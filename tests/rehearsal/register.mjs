// node --import ./tests/rehearsal/register.mjs e2e/live-e2e.mjs
//
// Runs the UNMODIFIED live script against a local stand-in instead of Studionet:
//   * genlayer-js / viem are replaced by shims (see hooks.mjs);
//   * the contract runs in the strict SDK stand-in, with a fake model (REHEARSAL_MODEL, see backend.py);
//   * the revision cooldown is shortened to 3 s in a temporary COPY of the contract (the script checks
//     the chain's limits against that same file, so the check stays meaningful);
//   * fixtures are served from e2e/fixtures; the report goes to reports/rehearsal/.
import { mkdtempSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { register } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

register("./hooks.mjs", import.meta.url);

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
process.env.E2E_FIXTURE_BASE ??= "https://raw.githubusercontent.com/rehearsal/redline/main/e2e/fixtures";
process.env.E2E_REPORT_DIR ??= join("reports", "rehearsal");
process.env.E2E_NETWORK_LABEL ??= `OFFLINE REHEARSAL (local stand-in, model: ${process.env.REHEARSAL_MODEL || "truthful"}) - NOT Studionet`;

// REHEARSAL_SOURCE lets scripts/rehearsal_mutants.py feed deliberately broken contracts to the live script.
const source = readFileSync(process.env.REHEARSAL_SOURCE || resolve(root, "contracts", "redline.py"), "utf8");
const fast = source.replace(/^MIN_APPEND_INTERVAL_SECONDS = \d+/m, "MIN_APPEND_INTERVAL_SECONDS = 3");
if (fast === source) throw new Error("rehearsal: could not shorten the cooldown (constant not found)");
const dir = mkdtempSync(join(tmpdir(), "redline-rehearsal-"));
writeFileSync(join(dir, "redline.py"), fast);
process.env.REDLINE_CONTRACT_PATH ??= join(dir, "redline.py");

const realFetch = globalThis.fetch;
globalThis.fetch = async (input, init) => {
  const url = String(input?.url ?? input);
  const base = process.env.E2E_FIXTURE_BASE;
  if (url.startsWith(base + "/")) {
    const file = resolve(root, "e2e", "fixtures", new URL(url).pathname.split("/").pop());
    return existsSync(file) ? new Response(readFileSync(file, "utf8"), { status: 200 }) : new Response("not found", { status: 404 });
  }
  return realFetch(input, init);
};
