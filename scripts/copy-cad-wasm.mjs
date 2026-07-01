import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const sourceDir = join(root, "node_modules", "@flyfish-dev", "cad-viewer", "dist", "wasm");
const targetDir = join(root, "public", "wasm");

const assets = [
  "dwg-worker.js",
  "libredwg-web.js",
  "libredwg-web.wasm",
  "dwfv-render.wasm",
];

mkdirSync(targetDir, { recursive: true });

for (const asset of assets) {
  copyFileSync(join(sourceDir, asset), join(targetDir, asset));
}
