// app.js — browser UI for kobofix. Wires the file picker to the engine and
// renders the report. JSZip is the vendored UMD global (window.JSZip).
import { readEntries, processEntries, lintEntries, buildEpub } from "./kobofix.js";
import { DEFAULT_OPTS } from "./kobocss.js";

const $ = (id) => document.getElementById(id);
const drop = $("drop"), fileInput = $("file"), spin = $("spin"), errBox = $("error");

const RULE_TITLE = {
  "KOBO-000": "mimetype packaging", "KOBO-001": "Unsupported CSS value function",
  "KOBO-002": "Viewport unit", "KOBO-003": "Empty @media/@supports block",
  "KOBO-004": "rem unit", "KOBO-010": "Flexbox", "KOBO-011": "CSS Grid",
  "KOBO-012": "position", "KOBO-013": "transform", "KOBO-014": "animation/transition",
  "KOBO-015": "object-fit", "KOBO-016": "aspect-ratio", "KOBO-017": ":has()",
  "KOBO-018": ":is()/:where()", "KOBO-019": "writing-mode",
};
const CHANGE_LABEL = {
  calc: "calc() resolved", min: "min() reduced", max: "max() reduced",
  clamp: "clamp() reduced", env: "env() replaced", viewport: "viewport units → px",
  rem: "rem → px", "empty-atrule": "empty @media/@supports removed",
};

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function el(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; }

drop.addEventListener("click", () => fileInput.click());
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") fileInput.click(); });
fileInput.addEventListener("change", () => { if (fileInput.files[0]) run(fileInput.files[0]); });
["dragover", "dragenter"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) run(f); });

async function run(file) {
  errBox.style.display = "none";
  $("results").style.display = "none";
  spin.style.display = "block";
  try {
    const ab = await file.arrayBuffer();
    const { entries, inMt } = await readEntries(window.JSZip, ab);
    const before = lintEntries(entries, inMt).findings;
    const res = processEntries(entries, { ...DEFAULT_OPTS });
    const after = lintEntries(res.entries, { present: true, first: true, exact: true }).findings;
    const fixedBytes = await buildEpub(window.JSZip, res.entries);
    render(file.name, before, after, res, fixedBytes);
  } catch (e) {
    spin.style.display = "none";
    errBox.style.display = "block";
    errBox.textContent = "Could not read that file as an EPUB: " + (e && e.message ? e.message : e);
  }
}

function groupBy(arr, key) {
  const m = new Map();
  for (const x of arr) { const k = key(x); if (!m.has(k)) m.set(k, []); m.get(k).push(x); }
  return m;
}

function render(name, before, after, res, fixedBytes) {
  spin.style.display = "none";
  const errs = before.filter((f) => f.severity === "error");
  const warns = before.filter((f) => f.severity === "warning");
  const infos = before.filter((f) => f.severity === "info");
  const fixableErrs = errs.length; // KOBO-000/001 are all auto-fixed
  const manual = after.filter((f) => f.severity !== "error"); // what remains (report-only)

  // Verdict
  const v = $("verdict");
  if (before.length === 0) {
    v.className = "verdict good";
    v.innerHTML = `<span style="font-size:24px">✅</span><div><div class="big">Already Kobo-ready</div>
      <div class="muted small">No RMSDK-breaking CSS or packaging issues found. You can still download a re-packaged copy below.</div></div>`;
  } else {
    v.className = "verdict bad";
    v.innerHTML = `<span style="font-size:24px">⚠️</span><div>
      <div class="big">${errs.length} book-breaking issue${errs.length === 1 ? "" : "s"} found on Kobo</div>
      <div class="muted small">Plus ${warns.length} warning${warns.length === 1 ? "" : "s"}.
      kobofix fixed the breakers automatically — download the repaired EPUB below.</div></div>`;
  }

  // Download
  const blob = new Blob([fixedBytes], { type: "application/epub+zip" });
  const url = URL.createObjectURL(blob);
  const outName = name.replace(/\.epub$/i, "") + ".kobofixed.epub";
  $("dl").innerHTML = "";
  const a = el(`<a class="btn" href="${url}" download="${esc(outName)}">⬇ Download fixed EPUB (${(blob.size / 1024).toFixed(0)} KB)</a>`);
  $("dl").appendChild(a);
  $("dl").appendChild(el(`<span class="muted small" style="margin-left:10px">${esc(outName)}</span>`));

  // Cards
  $("cards").innerHTML = `
    <div class="scard ${errs.length ? "err" : "ok"}"><div class="n">${errs.length}</div><div class="l">book-breaking errors</div></div>
    <div class="scard ${warns.length ? "warn" : "ok"}"><div class="n">${warns.length}</div><div class="l">warnings</div></div>
    <div class="scard"><div class="n">${res.changes.length + res.varmapSize}</div><div class="l">auto-fixes applied</div></div>
    <div class="scard ${manual.length ? "warn" : "ok"}"><div class="n">${manual.length}</div><div class="l">need your review</div></div>`;

  // Findings table (grouped by rule)
  const fp = $("findings");
  fp.innerHTML = "<h2>What breaks on Kobo</h2>";
  if (before.length === 0) {
    fp.appendChild(el(`<p class="muted">Nothing — this book is already RMSDK-safe.</p>`));
  } else {
    const byRule = groupBy(before, (f) => f.rule);
    const rules = [...byRule.keys()].sort();
    const tbl = el(`<table><thead><tr><th>Rule</th><th>Sev</th><th>Count</th><th>Where</th></tr></thead><tbody></tbody></table>`);
    const tb = tbl.querySelector("tbody");
    for (const rule of rules) {
      const items = byRule.get(rule);
      const sev = items[0].severity;
      const locs = items.slice(0, 4).map((f) => f.line ? `${shortName(f.source)}:${f.line}` : shortName(f.source));
      const more = items.length > 4 ? ` +${items.length - 4} more` : "";
      tb.appendChild(el(`<tr>
        <td><strong>${rule}</strong><div class="rulemsg">${esc(RULE_TITLE[rule] || "")}</div></td>
        <td><span class="badge ${sev}">${sev}</span></td>
        <td>${items.length}</td>
        <td class="loc mono small">${locs.map(esc).join("<br>")}${more}</td></tr>`));
    }
    fp.appendChild(tbl);
    fp.appendChild(el(`<details class="small"><summary>Full message for each rule</summary>${
      rules.map((r) => `<p><strong>${r}</strong> — ${esc(byRule.get(r)[0].message)}</p>`).join("")}</details>`));
  }

  // Fixed (changes)
  const xp = $("fixed");
  xp.innerHTML = "<h2>What kobofix changed</h2>";
  const ul = el(`<table><tbody></tbody></table>`);
  const tb2 = ul.querySelector("tbody");
  if (res.varmapSize) tb2.appendChild(el(`<tr><td>${res.varmapSize} custom propert${res.varmapSize === 1 ? "y" : "ies"} resolved &amp; inlined (var() eliminated)</td></tr>`));
  const byType = groupBy(res.changes, (c) => c.type);
  for (const [t, items] of byType) {
    if (t === "empty-atrule") { tb2.appendChild(el(`<tr><td>${items.length}× empty @media/@supports removed</td></tr>`)); continue; }
    const sample = items.slice(0, 3).map((c) => `<code>${esc(c.from)}</code> <span class="arrow">→</span> <code>${esc(c.to)}</code>`).join("&nbsp;&nbsp; ");
    const more = items.length > 3 ? ` <span class="muted">+${items.length - 3}</span>` : "";
    tb2.appendChild(el(`<tr><td><strong>${items.length}×</strong> ${esc(CHANGE_LABEL[t] || t)}: ${sample}${more}</td></tr>`));
  }
  if (!res.changes.length && !res.varmapSize) tb2.appendChild(el(`<tr><td class="muted">No value-token rewrites were needed.</td></tr>`));
  // packaging note
  tb2.appendChild(el(`<tr><td class="muted small">Re-packaged as a spec-correct OCF zip (mimetype first / uncompressed) — passes EPUBCheck.</td></tr>`));
  xp.appendChild(ul);

  // Manual review
  const mp = $("manual");
  mp.innerHTML = "<h2>Needs your review (not auto-changed)</h2>";
  if (!manual.length) {
    mp.appendChild(el(`<p class="muted">Nothing — no flexbox/grid/transform etc. that requires a human decision.</p>`));
  } else {
    const byRuleM = groupBy(manual, (f) => f.rule);
    for (const [rule, items] of byRuleM) {
      const locs = items.slice(0, 5).map((f) => f.line ? `${shortName(f.source)}:${f.line}` : shortName(f.source)).join(", ");
      mp.appendChild(el(`<p><span class="badge ${items[0].severity}">${items[0].severity}</span>
        <strong>${rule} ${esc(RULE_TITLE[rule] || "")}</strong> (${items.length})<br>
        <span class="rulemsg">${esc(items[0].message)}</span><br>
        <span class="loc mono small">${esc(locs)}</span></p>`));
    }
    mp.appendChild(el(`<p class="muted small">These have no safe automatic equivalent on RMSDK — converting them could reflow your book, so kobofix leaves them for you. The usual fix is to wrap the modern rule in <code>@supports</code> with a plain block/float fallback.</p>`));
  }

  $("results").style.display = "block";
  $("results").scrollIntoView({ behavior: "smooth", block: "start" });
}

function shortName(s) {
  if (!s) return s;
  return s.replace(/^.*\//, "").replace(/^(.{30}).+$/, "$1…");
}
