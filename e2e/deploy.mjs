#!/usr/bin/env node
// Deploys contracts/redline.py to GenLayer Studionet and prints the address.
//   REDLINE_PRIVATE_KEY=0x...   optional (a throw-away key is generated otherwise)
//   node e2e/deploy.mjs
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { createAccount, deployRedline, EXPLORER, fund, generatePrivateKey, makeClient, ROOT } from "./lib.mjs";

const key = process.env.REDLINE_PRIVATE_KEY || generatePrivateKey();
const account = createAccount(key);
const client = makeClient(account);
const funded = await fund(client, account.address);
console.log(`deployer ${account.address}${funded === true ? " (funded via sim_fundAccount)" : ` (funding note: ${funded})`}`);

const { address, hash } = await deployRedline(client);

let version = "?";
try {
  version = String(await client.readContract({ address, functionName: "get_version", args: [] }));
} catch (err) {
  version = `unreadable yet (${String(err?.message ?? err).slice(0, 80)})`;
}

const record = { network: "studionet", address, deployTx: hash, deployer: account.address, version, deployedAt: new Date().toISOString() };
mkdirSync(resolve(ROOT, "deployments"), { recursive: true });
writeFileSync(resolve(ROOT, "deployments", "studionet.json"), JSON.stringify(record, null, 2) + "\n");

console.log(`\nRedline ${version} deployed
  address : ${address}
  tx      : ${EXPLORER}/tx/${hash}
  contract: ${EXPLORER}/contracts/${address}
  record  : deployments/studionet.json

Next:
  export REDLINE_CONTRACT_ADDRESS=${address}
  npm run e2e`);
