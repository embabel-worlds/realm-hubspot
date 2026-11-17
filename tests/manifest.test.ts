/**
 * The shipped `dist` is coherent: every method `dist/manifest.json` advertises resolves to a
 * function on what `dist/api/*.js` exports, loaded by Node's own `require` the way the host loads
 * it, so `@embabel/runtime-types` comes from the vendored `dist/node_modules` copy. A stale vendored
 * runtime (one without a method the manifest names, such as `Entity.is`) fails here.
 */
/// <reference types="vite/client" />
import { describe, it, expect } from "vitest";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Json = any;
// The realm carries no @types/node; load Node builtins untyped.
const builtin = (name: string): Promise<Json> => import(/* @vite-ignore */ name);

describe("dist/manifest.json", () => {
  it("names only methods the shipped classes and modules define", async () => {
    const { createRequire } = await builtin("node:module");
    const { readdirSync } = await builtin("node:fs");
    const require = createRequire(import.meta.url);
    const dist = require.resolve("../dist/manifest.json").replace(/manifest\.json$/, "");
    const manifest: Json = require(`${dist}manifest.json`);

    const modules: Record<string, Json> = Object.fromEntries(
      readdirSync(`${dist}api`).filter((f: string) => f.endsWith(".js"))
        .map((f: string) => [f.replace(/\.js$/, "").replace(/-/g, "_"), require(`${dist}api/${f}`)]),
    );
    expect(manifest.entries.length).toBeGreaterThan(0);

    const unresolved = manifest.entries.filter((e: Json) => {
      const mod = modules[e.namespace];
      const target = e.className ? mod?.[e.className]?.prototype : mod;
      return typeof target?.[e.name] !== "function";
    }).map((e: Json) => `${e.namespace}.${e.className ?? ""}.${e.name}`);
    expect(unresolved).toEqual([]);
  });
});
