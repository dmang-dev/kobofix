#!/usr/bin/env node
// kobofix — Node CLI. Mirrors the core of the Python tool: fix an EPUB for Kobo,
// or --check (lint) it. Pure Node + JSZip; the same engine that powers the web app.
import { readFile, writeFile } from "node:fs/promises";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import JSZip from "jszip";
import { readEntries, processEntries, lintEntries, buildEpub } from "./kobofix.js";
import { DEFAULT_OPTS } from "./kobocss.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const VERSION = JSON.parse(readFileSync(join(HERE, "package.json"), "utf8")).version;

function help() {
  console.log(`kobofix ${VERSION} - make an EPUB safe for Kobo (Adobe RMSDK)

Usage:
  kobofix <book.epub> [-o out.epub]      fix and write a repaired EPUB
  kobofix --check <book.epub> [--json]   report Kobo/RMSDK issues, write nothing

Options:
  -o, --output PATH   output file (default: <input>.kobofixed.epub)
  --check             lint only; exit 2 if any book-breaking issue
  --json              machine-readable report (with --check)
  --strict            exit 1 if --check finds any issue (even warnings)
  -h, --help          this help
  --version           print version`);
}

const argv = process.argv.slice(2);
let input = null, output = null, check = false, json = false, strict = false;
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (a === "--check") check = true;
  else if (a === "-o" || a === "--output") output = argv[++i];
  else if (a === "--json" || a === "--report=json") json = true;
  else if (a === "--strict") strict = true;
  else if (a === "-h" || a === "--help") { help(); process.exit(0); }
  else if (a === "--version") { console.log("kobofix " + VERSION); process.exit(0); }
  else if (!a.startsWith("-")) input = a;
}

if (!input) { help(); process.exit(2); }
if (!existsSync(input)) { console.error("kobofix: input not found: " + input); process.exit(2); }

const short = (s) => (s || "").replace(/^.*\//, "");

const buf = await readFile(input);
const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
const { entries, inMt } = await readEntries(JSZip, ab);

if (check) {
  const findings = lintEntries(entries, inMt).findings;
  if (json) {
    console.log(JSON.stringify({ version: VERSION, input, findings }, null, 2));
  } else {
    const byRule = new Map();
    for (const f of findings) { if (!byRule.has(f.rule)) byRule.set(f.rule, []); byRule.get(f.rule).push(f); }
    console.log(`\nkobofix --check : ${input}\n`);
    if (!findings.length) console.log("  OK  no RMSDK-breaking CSS or packaging issues found");
    for (const rule of [...byRule.keys()].sort()) {
      const items = byRule.get(rule);
      console.log(`  ${rule.padEnd(9)} ${items[0].severity.toUpperCase().padEnd(7)} ${items.length}x`);
      console.log("      " + items[0].message.slice(0, 100));
      for (const f of items.slice(0, 4)) console.log(`        - ${short(f.source)}${f.line ? ":" + f.line : ""}  ${f.snippet}`);
      if (items.length > 4) console.log(`        ... and ${items.length - 4} more`);
    }
    const e = findings.filter((f) => f.severity === "error").length;
    const w = findings.filter((f) => f.severity === "warning").length;
    console.log(`\n  Summary: ${e} error, ${w} warning` + (findings.length ? "  (run `kobofix " + input + "` to auto-fix)" : ""));
  }
  const hasErr = findings.some((f) => f.severity === "error");
  process.exit(hasErr ? 2 : (strict && findings.length ? 1 : 0));
}

// Fix mode
const res = processEntries(entries, { ...DEFAULT_OPTS });
const fixed = await buildEpub(JSZip, res.entries);
const out = output || input.replace(/\.epub$/i, "") + ".kobofixed.epub";
await writeFile(out, fixed);

const errs = lintEntries(entries, inMt).findings.filter((f) => f.severity === "error").length;
console.log(`kobofix: fixed ${res.changes.length + res.varmapSize} construct(s) across ${res.edited.length} file(s)` +
  (res.warnings.length ? `, ${res.warnings.length} item(s) flagged for manual review` : "") + ".");
console.log(`  ${errs} book-breaking issue(s) resolved; mimetype re-packaged (OCF-correct).`);
console.log("  wrote " + out);
process.exit(0);
