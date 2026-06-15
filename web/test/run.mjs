// Headless test: run the browser engine in Node and verify parity with the
// Python CLI + OCF correctness of the produced epub.
import JSZip from "jszip";
import { readFileSync, writeFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";
import {
  readEntries, processEntries, lintEntries, buildEpub, decodeText, classify, parseManifest,
} from "../kobofix.js";
import { splitCodeSegments } from "../kobocss.js";

const here = dirname(fileURLToPath(import.meta.url));
const ROOT = join(here, "..", "..");
const OPTS = { rem: true, rootFontSize: 16, viewport: true, vwBase: 600, vhBase: 800, clampPick: "pref" };
const BANNED = ["calc(", "min(", "max(", "clamp(", "var(", "env("];

let failures = 0;
function ok(cond, label) {
  console.log((cond ? "  ok   " : "  FAIL ") + label);
  if (!cond) failures++;
}

function verifyOcf(bytes) {
  // Parse the first local file header directly.
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const sig = dv.getUint32(0, true);
  if (sig !== 0x04034b50) return "bad local-header signature";
  const method = dv.getUint16(8, true);
  const nameLen = dv.getUint16(26, true);
  const extraLen = dv.getUint16(28, true);
  const name = new TextDecoder().decode(bytes.subarray(30, 30 + nameLen));
  const data = new TextDecoder().decode(bytes.subarray(30 + nameLen + extraLen, 30 + nameLen + extraLen + 20));
  if (name !== "mimetype") return "first entry is '" + name + "', not 'mimetype'";
  if (method !== 0) return "mimetype is compressed (method " + method + ")";
  if (extraLen !== 0) return "mimetype has a ZIP extra field (" + extraLen + " bytes)";
  if (data !== "application/epub+zip") return "mimetype bytes wrong: " + JSON.stringify(data);
  return null;
}

function codeOf(text) {
  return splitCodeSegments(text).filter((s) => s.code).map((s) => s.text).join("");
}

async function testBook(relPath) {
  console.log("\n#### " + relPath + " ####");
  const buf = readFileSync(join(ROOT, relPath));
  const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);

  const { entries, inMt } = await readEntries(JSZip, ab);
  const lint = lintEntries(entries, inMt);
  const counts = {};
  for (const f of lint.findings) counts[f.rule] = (counts[f.rule] || 0) + 1;
  console.log("  lint findings:", lint.findings.length, JSON.stringify(counts));

  const res = processEntries(entries, OPTS);
  console.log("  edited:", res.edited.length, "| vars inlined:", res.varmapSize,
    "| changes:", res.changes.length, "| warnings:", res.warnings.length);

  // No banned value tokens may remain in any css/markup output.
  const media = parseManifest(res.entries);
  for (const { name, bytes } of res.entries) {
    const k = classify(name, media);
    if (k !== "css" && k !== "markup") continue;
    const code = codeOf(decodeText(bytes)).toLowerCase();
    for (const b of BANNED) ok(!code.includes(b), `${name} free of ${b}`);
  }

  const fixed = await buildEpub(JSZip, res.entries);
  const outName = "out-" + relPath.replace(/[\\/]/g, "_").replace(/\.epub$/, "") + ".epub";
  writeFileSync(join(here, outName), fixed);
  const ocf = verifyOcf(fixed);
  ok(ocf === null, "OCF packaging valid" + (ocf ? " (" + ocf + ")" : ""));
  console.log("  wrote", join("web", "test", outName));
}

await testBook("sample-validpkg-badcss.epub");
await testBook(join("samples", "alice-modern.epub"));

console.log("\n" + (failures ? `FAILED (${failures})` : "ALL JS-PORT CHECKS PASSED"));
process.exit(failures ? 1 : 0);
