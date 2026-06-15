// kobofix.js — EPUB pipeline + zip glue on top of the kobocss engine.
// Operates on `entries` = [{name, bytes:Uint8Array}]. JSZip is injected so the
// same code runs in the browser (vendored jszip) and in Node (npm jszip).

import {
  collectCustomProperties, collectXhtmlVars, sanitizeCssText, processXhtml,
  retagToUtf8, lintFragment, lintMarkup,
} from "./kobocss.js";

export const MIMETYPE_BYTES = new TextEncoder().encode("application/epub+zip");
const SKIP_PREFIX = ["__MACOSX/"];
const SKIP_SUFFIX = [".DS_Store", "Thumbs.db"];

// ---- text helpers ----
export function decodeText(bytes) {
  if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf)
    return new TextDecoder("utf-8").decode(bytes.subarray(3));
  if (bytes.length >= 2 && bytes[0] === 0xff && bytes[1] === 0xfe)
    return new TextDecoder("utf-16le").decode(bytes);
  if (bytes.length >= 2 && bytes[0] === 0xfe && bytes[1] === 0xff)
    return new TextDecoder("utf-16be").decode(bytes);
  return new TextDecoder("utf-8").decode(bytes);
}
export function encodeText(str) { return new TextEncoder().encode(str); }

function bytesEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

// ---- name classification ----
export function isCssName(n) { return n.toLowerCase().endsWith(".css"); }
export function isXhtmlName(n) {
  const l = n.toLowerCase();
  return l.endsWith(".xhtml") || l.endsWith(".html") || l.endsWith(".htm") || l.endsWith(".xht");
}
export function isSvgName(n) { return n.toLowerCase().endsWith(".svg"); }
export function isMimetype(n) {
  return n === "mimetype" || n.split("/").pop().toLowerCase() === "mimetype";
}

export function classify(name, media) {
  const mt = media[name];
  if (isCssName(name) || mt === "text/css") return "css";
  if (isXhtmlName(name) || isSvgName(name) ||
      ["application/xhtml+xml", "text/html", "image/svg+xml"].includes(mt)) return "markup";
  return null;
}

// ---- OPF manifest media-types ----
function normPosix(p) {
  const out = [];
  for (const s of p.split("/")) {
    if (s === "" || s === ".") continue;
    if (s === "..") { if (out.length && out[out.length - 1] !== "..") out.pop(); else out.push(s); }
    else out.push(s);
  }
  return out.join("/");
}

export function parseManifest(entries) {
  const table = {};
  const container = entries.find(
    (e) => e.name.replace(/\\/g, "/").toLowerCase() === "meta-inf/container.xml");
  if (!container) return table;
  const ctext = decodeText(container.bytes);
  const rootM = /<rootfile\b[^>]*\bfull-path\s*=\s*"([^"]*)"/i.exec(ctext);
  if (!rootM) return table;
  const opfPath = rootM[1];
  const opf = entries.find((e) => e.name === opfPath);
  if (!opf) return table;
  const otext = decodeText(opf.bytes);
  const opfDir = opfPath.includes("/") ? opfPath.slice(0, opfPath.lastIndexOf("/")) : "";
  for (const tag of otext.matchAll(/<item\b[^>]*>/gi)) {
    const t = tag[0];
    const hm = /\bhref\s*=\s*"([^"]*)"/i.exec(t);
    const mm = /\bmedia-type\s*=\s*"([^"]*)"/i.exec(t);
    if (!hm || !mm) continue;
    let href = hm[1];
    try { href = decodeURIComponent(href); } catch (_) { /* keep raw */ }
    const full = opfDir ? normPosix(opfDir + "/" + href) : normPosix(href);
    table[full] = mm[1];
  }
  return table;
}

export function packagingFixes(inMt) {
  const f = [];
  if (!inMt.present) f.push("added a missing mimetype entry");
  else {
    if (!inMt.first) f.push("moved mimetype to be the FIRST zip entry");
    if (!inMt.exact) f.push("rewrote mimetype to exact bytes 'application/epub+zip'");
  }
  return f;
}

// ---- core pipeline (no zip) ----
export function processEntries(entries, opts) {
  const media = parseManifest(entries);
  const varmap = new Map();
  for (const { name, bytes } of entries) {
    const k = classify(name, media);
    if (k === "css") collectCustomProperties(decodeText(bytes), varmap);
    else if (k === "markup") collectXhtmlVars(decodeText(bytes), varmap);
  }
  const changes = [], warnings = [], edited = [], out = [];
  for (const { name, bytes } of entries) {
    if (isMimetype(name)) continue; // re-emitted by buildEpub
    const k = classify(name, media);
    if (k === "css") {
      const text = decodeText(bytes);
      let nt = sanitizeCssText(text, varmap, opts, changes, warnings, name, true);
      nt = retagToUtf8(nt, true);
      if (nt !== text) edited.push(name);
      out.push({ name, bytes: encodeText(nt) });
    } else if (k === "markup") {
      const text = decodeText(bytes);
      let nt = processXhtml(text, varmap, opts, changes, warnings, name);
      nt = retagToUtf8(nt, false);
      if (nt !== text) edited.push(name);
      out.push({ name, bytes: encodeText(nt) });
    } else {
      out.push({ name, bytes });
    }
  }
  return { entries: out, changes, warnings, edited, varmapSize: varmap.size };
}

export function lintEntries(entries, inMt) {
  const media = parseManifest(entries);
  const findings = [];
  for (const f of packagingFixes(inMt)) {
    findings.push({
      engine: "kobofix", rule: "KOBO-000", severity: "error", source: "(package)",
      line: 0, snippet: "mimetype", fixable: true,
      message: "Packaging: " + f + " - readers (Kobo especially) report a mis-packaged mimetype as 'corrupted'.",
    });
  }
  for (const { name, bytes } of entries) {
    const k = classify(name, media);
    if (k === "css") { const t = decodeText(bytes); lintFragment(t, 0, t, name, findings); }
    else if (k === "markup") { const t = decodeText(bytes); lintMarkup(t, name, findings); }
  }
  return { findings };
}

// ---- zip glue (JSZip injected) ----
export async function readEntries(JSZip, arrayBuffer) {
  const zip = await JSZip.loadAsync(arrayBuffer);
  const entries = [];
  let firstFile = null;
  for (const name of Object.keys(zip.files)) {
    const f = zip.files[name];
    if (f.dir) continue;
    if (SKIP_PREFIX.some((p) => name.startsWith(p)) || SKIP_SUFFIX.some((s) => name.endsWith(s))) continue;
    const bytes = await f.async("uint8array");
    if (firstFile === null) firstFile = name;
    entries.push({ name, bytes });
  }
  const mt = entries.find((e) => e.name === "mimetype");
  const inMt = {
    present: entries.some((e) => isMimetype(e.name)),
    first: firstFile === "mimetype",
    exact: mt ? bytesEqual(mt.bytes, MIMETYPE_BYTES) : false,
  };
  return { entries, inMt };
}

export async function buildEpub(JSZip, entries) {
  const zip = new JSZip();
  zip.file("mimetype", MIMETYPE_BYTES, { compression: "STORE" });
  for (const { name, bytes } of entries) {
    if (isMimetype(name)) continue;
    zip.file(name, bytes, { compression: "DEFLATE", compressionOptions: { level: 9 } });
  }
  return zip.generateAsync({ type: "uint8array", mimeType: "application/epub+zip" });
}
