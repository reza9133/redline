// Just what e2e/live-e2e.mjs imports: toHex(string) and keccak256(hex). The Keccak here is an
// independent JavaScript implementation, so the contract's hashes (computed by the Python stand-in)
// are cross-checked by a second implementation when the live script recomputes the hash chain.
const MASK = (1n << 64n) - 1n;
const RC = [
  0x0000000000000001n, 0x0000000000008082n, 0x800000000000808an, 0x8000000080008000n, 0x000000000000808bn, 0x0000000080000001n,
  0x8000000080008081n, 0x8000000000008009n, 0x000000000000008an, 0x0000000000000088n, 0x0000000080008009n, 0x000000008000000an,
  0x000000008000808bn, 0x800000000000008bn, 0x8000000000008089n, 0x8000000000008003n, 0x8000000000008002n, 0x8000000000000080n,
  0x000000000000800an, 0x800000008000000an, 0x8000000080008081n, 0x8000000000008080n, 0x0000000080000001n, 0x8000000080008008n,
];
const ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]];
const rol = (v, n) => (n % 64 === 0 ? v : ((v << BigInt(n % 64)) | (v >> BigInt(64 - (n % 64)))) & MASK);

function keccakF(s) {
  for (let round = 0; round < 24; round++) {
    const c = [0, 1, 2, 3, 4].map((x) => s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20]);
    const d = [0, 1, 2, 3, 4].map((x) => c[(x + 4) % 5] ^ rol(c[(x + 1) % 5], 1));
    for (let x = 0; x < 5; x++) for (let y = 0; y < 5; y++) s[x + 5 * y] ^= d[x];
    const b = new Array(25).fill(0n);
    for (let x = 0; x < 5; x++) for (let y = 0; y < 5; y++) b[y + 5 * ((2 * x + 3 * y) % 5)] = rol(s[x + 5 * y], ROT[x][y]);
    for (let x = 0; x < 5; x++) for (let y = 0; y < 5; y++) s[x + 5 * y] = b[x + 5 * y] ^ (~b[((x + 1) % 5) + 5 * y] & MASK & b[((x + 2) % 5) + 5 * y]);
    s[0] ^= RC[round];
  }
}

export function toHex(value) {
  const bytes = typeof value === "string" ? Buffer.from(value, "utf8") : Buffer.from(value);
  return "0x" + bytes.toString("hex");
}

export function keccak256(input) {
  const data = typeof input === "string" ? Buffer.from(input.replace(/^0x/, ""), "hex") : Buffer.from(input);
  const rate = 136;
  const padded = Buffer.concat([data, Buffer.from([0x01])]);
  const total = Math.ceil(padded.length / rate) * rate;
  const block = Buffer.alloc(total);
  padded.copy(block);
  block[total - 1] |= 0x80;
  const state = new Array(25).fill(0n);
  for (let off = 0; off < total; off += rate) {
    for (let i = 0; i < rate / 8; i++) state[i] ^= block.readBigUInt64LE(off + 8 * i);
    keccakF(state);
  }
  const out = Buffer.alloc(32);
  for (let i = 0; i < 4; i++) out.writeBigUInt64LE(state[i], 8 * i);
  return "0x" + out.toString("hex");
}
