import { build } from "esbuild";
import { readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";

const projectRoot = path.resolve(import.meta.dirname, "..");
const output = path.join(projectRoot, "src/cryptobox/static/preview-host.js");

const result = await build({
  entryPoints: [path.join(projectRoot, "src/cryptobox/static/preview-host-src.js")],
  bundle: true,
  minify: true,
  platform: "browser",
  format: "iife",
  outfile: output,
  metafile: true,
});

function packageRootFor(input) {
  const parts = path.normalize(input).split(path.sep);
  const nodeModules = parts.lastIndexOf("node_modules");
  if (nodeModules < 0 || nodeModules + 1 >= parts.length) return null;
  const length = parts[nodeModules + 1].startsWith("@") ? 2 : 1;
  return parts.slice(0, nodeModules + 1 + length).join(path.sep);
}

const packages = new Map();
for (const input of Object.keys(result.metafile.inputs)) {
  const relativeRoot = packageRootFor(input);
  if (!relativeRoot) continue;
  const absoluteRoot = path.resolve(projectRoot, relativeRoot);
  try {
    const metadata = JSON.parse(await readFile(path.join(absoluteRoot, "package.json"), "utf8"));
    packages.set(`${metadata.name}@${metadata.version}`, { absoluteRoot, metadata });
  } catch (_) {
    // Some generated package fragments do not carry package metadata.
  }
}

const notice = [
  "Cryptobox web preview - third-party notices",
  "=============================================",
  "",
  "The following packages are bundled into preview-host.js and execute only in",
  "the local, sandboxed preview frame. Their license texts are reproduced below.",
  "",
];

for (const [identity, { absoluteRoot, metadata }] of [...packages.entries()].sort()) {
  notice.push("", identity, "-".repeat(identity.length));
  notice.push(`License: ${metadata.license || "See included license text"}`);
  if (metadata.homepage) notice.push(`Homepage: ${metadata.homepage}`);
  const names = await readdir(absoluteRoot);
  const licenseFiles = names.filter((name) => /^(licen[cs]e|notice|copying)(\.|$)/i.test(name)).sort();
  if (!licenseFiles.length) {
    notice.push("License file was not included in the installed package.");
    continue;
  }
  for (const name of licenseFiles) {
    notice.push("", `[${name}]`, await readFile(path.join(absoluteRoot, name), "utf8"));
  }
}

await writeFile(
  path.join(projectRoot, "src/cryptobox/static/THIRD_PARTY_NOTICES.txt"),
  `${notice.join("\n").trim()}\n`,
  "utf8",
);
