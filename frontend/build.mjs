import { build } from "esbuild";
import { mkdir, copyFile } from "node:fs/promises";
await mkdir("dist", { recursive: true });
await build({ entryPoints: ["main.js"], bundle: true, format: "esm", target: "es2022",
  outfile: "dist/app.js", minify: true });
await copyFile("index.html", "dist/index.html");
await copyFile("style.css", "dist/style.css");
