// Module-resolution hook: points the live script's imports of genlayer-js and viem at the shims.
const SHIMS = {
  "genlayer-js": "./shims/genlayer-js.mjs",
  "genlayer-js/chains": "./shims/chains.mjs",
  "genlayer-js/types": "./shims/types.mjs",
  viem: "./shims/viem.mjs",
};

export async function resolve(specifier, context, nextResolve) {
  if (SHIMS[specifier]) return { url: new URL(SHIMS[specifier], import.meta.url).href, shortCircuit: true };
  return nextResolve(specifier, context);
}
