// A stand-in for the parts of genlayer-js that the Redline scripts use. Every call is forwarded to
// tests/rehearsal/backend.py, which runs the contract inside the strict SDK stand-in.
import { spawn } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { dirname, resolve } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const ZERO = "0x" + "00".repeat(20);
let proc = null;
const pending = new Map();
let nextId = 1;

function backend() {
  if (proc) return proc;
  proc = spawn(process.env.PYTHON || "python3", [resolve(here, "..", "backend.py")], { stdio: ["pipe", "pipe", "inherit"], env: process.env });
  createInterface({ input: proc.stdout }).on("line", (line) => {
    const msg = JSON.parse(line);
    const resolveReq = pending.get(msg.id);
    pending.delete(msg.id);
    resolveReq?.(msg);
  });
  proc.on("exit", (code) => {
    for (const r of pending.values()) r({ error: `BACKEND EXITED (${code})` });
    pending.clear();
  });
  process.on("exit", () => proc.kill());
  return proc;
}

const rpc = (req) =>
  new Promise((done) => {
    const id = nextId++;
    pending.set(id, done);
    backend().stdin.write(JSON.stringify({ ...req, id }) + "\n");
  });

const encodeArgs = (args = []) => JSON.parse(JSON.stringify(args, (_k, v) => (typeof v === "bigint" ? Number(v) : v)));

function decode(v) {
  if (v && typeof v === "object" && !Array.isArray(v) && v.$t) {
    if (v.$t === "int") return BigInt(v.v);
    if (v.$t === "addr") return Uint8Array.from(Buffer.from(v.v.slice(2), "hex"));
    if (v.$t === "map") return new Map(v.v.map(([k, x]) => [k, decode(x)]));
  }
  return Array.isArray(v) ? v.map(decode) : v;
}

export function generatePrivateKey() {
  return "0x" + randomBytes(32).toString("hex");
}

export function createAccount(privateKey) {
  return { privateKey, address: "0x" + createHash("sha256").update(String(privateKey)).digest("hex").slice(0, 40) };
}

export function createClient({ account } = {}) {
  const sender = account?.address ?? ZERO;
  const fail = (r) => {
    if (!r.error) return;
    const err = new Error(r.error);
    if (r.detail) err.details = r.detail;
    throw err;
  };
  return {
    account,
    async request({ method }) {
      if (method === "sim_fundAccount") return true;
      throw new Error(`rehearsal: unsupported RPC method ${method}`);
    },
    async deployContract({ code }) {
      const r = await rpc({ op: "deploy", code });
      fail(r);
      return r.hash;
    },
    async readContract({ address, functionName, args }) {
      const r = await rpc({ op: "read", address, fn: functionName, args: encodeArgs(args), sender });
      fail(r);
      return decode(r.result);
    },
    async writeContract({ address, functionName, args }) {
      const r = await rpc({ op: "write", address, fn: functionName, args: encodeArgs(args), sender });
      fail(r);
      return r.hash;
    },
    async simulateWriteContract({ address, functionName, args }) {
      const r = await rpc({ op: "simulate", address, fn: functionName, args: encodeArgs(args), sender });
      fail(r);
      return null;
    },
    async waitForTransactionReceipt({ hash }) {
      const { receipt } = await rpc({ op: "receipt", hash });
      if (receipt.status === "ok") {
        return { txExecutionResultName: "FINISHED_WITH_SUCCESS", status: "ACCEPTED", data: receipt.contract_address ? { contract_address: receipt.contract_address } : {} };
      }
      if (receipt.status === "error") {
        // the contract's own text sits deep inside the receipt, like on the real network
        return { txExecutionResultName: "FINISHED_WITH_ERROR", status: "ACCEPTED", consensus_data: { leader_receipt: [{ result: { payload: { readable: `UserError: ${receipt.message}` } } }] } };
      }
      throw new Error("rehearsal: the validators did not reach consensus (UNDETERMINED); the real client would time out waiting for ACCEPTED");
    },
  };
}
