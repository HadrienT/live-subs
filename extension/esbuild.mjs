// Bundles one file per extension context into dist/, then copies static assets
// and writes the manifest with the version taken from package.json.
import { build, context } from "esbuild";
import { cpSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";

const watch = process.argv.includes("--watch");
const pkg = JSON.parse(readFileSync("package.json", "utf8"));

const options = {
  entryPoints: {
    content: "src/content/index.ts",
    background: "src/background/index.ts",
    worklet: "src/worklet/processor.ts",
    popup: "src/popup/index.ts",
    options: "src/options/index.ts",
  },
  outdir: "dist",
  bundle: true,
  format: "iife",
  target: "firefox142",
  sourcemap: watch ? "inline" : false,
  logLevel: "info",
};

function copyStatic() {
  mkdirSync("dist", { recursive: true });
  cpSync("static", "dist", { recursive: true });
  const manifest = JSON.parse(readFileSync("manifest.json", "utf8"));
  manifest.version = pkg.version;
  writeFileSync("dist/manifest.json", JSON.stringify(manifest, null, 2));
}

copyStatic();
if (watch) {
  const ctx = await context(options);
  await ctx.watch();
} else {
  await build(options);
}
