// kobocss.js — the kobofix CSS engine, ported from kobofix.py.
// Pure string functions, no DOM/zip/Node deps. Runs in the browser and Node.
// Mirrors the Python implementation function-for-function so the web app and
// the CLI produce identical output.

export const MATH_FUNCS = new Set(["calc", "min", "max", "clamp", "env"]);

export const ABSOLUTE_PX = {
  px: 1.0, pt: 96.0 / 72.0, pc: 16.0, in: 96.0,
  cm: 96.0 / 2.54, mm: 96.0 / 25.4, q: 96.0 / 25.4 / 4.0,
};
const VIEWPORT_UNITS = new Set(["vw", "vh", "vmin", "vmax"]);

const IDENT_RE = /-?[A-Za-z_][A-Za-z0-9_-]*/y;
const NUM_SPLIT_RE = /^([+-]?\d*\.?\d+(?:[eE][+-]?\d+)?)([A-Za-z%]*)$/;
const NUMUNIT_RE = /\d*\.?\d+(?:[eE][+-]?\d+)?[A-Za-z%]*/y;
const VENDOR_RE = /^-(?:webkit|moz|o|ms)-/;
const URL_OPEN_RE = /url\(/iy;

const REM_RE = /(?<![\w.#])(-?\d*\.?\d+(?:[eE][+-]?\d+)?)rem\b/gi;
const VPUNIT_RE = /(?<![\w.#])(-?\d*\.?\d+(?:[eE][+-]?\d+)?)(vmin|vmax|vw|vh)\b/gi;
const EMPTY_ATRULE_RE = /@(?:media|supports|document|-[\w-]+)\b[^{}]*\{\s*\}/gi;
const EMPTY_RULESET_RE = /(?<![@\w-])([^{}@;]+?)\{\s*\}/g;
const EMPTY_DECL_RE = /([A-Za-z-]+)\s*:\s*(?=[;}]);?/g;
const DECL_VAR_RE = /(--[A-Za-z0-9_-]+)\s*:/g;

export const FUNC_LINT_RE = /(?:-(?:webkit|moz|o|ms)-)?(calc|min|max|clamp|var|env)\s*\(/gi;
export const XML_ENC_RE = /(<\?xml\b[^>]*?\bencoding\s*=\s*["'])([^"']*)(["'])/i;
export const CSS_CHARSET_RE = /^@charset\s+"([^"]*)";/i;
export const STYLE_BLOCK_RE = /(<style\b[^>]*>)([\s\S]*?)(<\/style>)/gi;
export const STYLE_ATTR_RE = /(\sstyle\s*=\s*)(["'])([\s\S]*?)(\2)/gi;
export const MARKUP_SKIP_RE =
  /(<style\b[^>]*>[\s\S]*?<\/style>|<script\b[^>]*>[\s\S]*?<\/script>|<!--[\s\S]*?-->)/gi;

// Report-only layout features: detect & warn, never auto-rewrite.
export const REPORT_ONLY = [
  ["flexbox", /display\s*:\s*(?:inline-)?flex/gi,
    "Flexbox: RMSDK ignores it; flex children fall back to block flow. " +
    "Side-by-side/justify/order layouts will break. Rework with float/table or wrap in @supports."],
  ["grid", /display\s*:\s*(?:inline-)?grid/gi,
    "CSS Grid: unsupported by RMSDK; tracks/areas are lost and items stack. No mechanical fallback."],
  ["grid-props", /\bgrid-(?:template|area|auto|column|row)\b/gi,
    "Grid sub-property: only meaningful with display:grid, which RMSDK ignores."],
  ["position", /position\s*:\s*(?:sticky|fixed|absolute)/gi,
    "position:absolute/fixed/sticky in reflowable EPUB is unreliable on RMSDK and can push content off-page."],
  ["transform", /(?<![\w-])transform\s*:/gi,
    "CSS transform is ignored by RMSDK (no rotation/scale). Decorative-only is harmless; load-bearing breaks."],
  ["animation", /@keyframes|\banimation\s*:|\btransition\s*:/gi,
    "Animations/transitions are dropped on the eInk/RMSDK path. Ensure the resting style is the final frame."],
  ["object-fit", /\bobject-fit\s*:/gi,
    "object-fit is unsupported by RMSDK; images stretch to declared width/height instead of cropping."],
  ["aspect-ratio", /\baspect-ratio\s*:/gi,
    "aspect-ratio is unknown to RMSDK (silently dropped). Provide explicit width/height if the ratio matters."],
  ["has", /:has\(/gi,
    ":has() has no equivalent on RMSDK and the whole rule may be dropped. Rewrite by hand."],
  ["is-where", /:(?:is|where)\(/gi,
    ":is()/:where() may not parse on RMSDK. Expand to a comma-separated selector list by hand."],
  ["writing-mode", /\bwriting-mode\s*:/gi,
    "writing-mode: Kobo DOES support this for vertical CJK text -- leave it alone unless you know you don't need it."],
];

export const LAYOUT_RULE = {
  flexbox: "KOBO-010", grid: "KOBO-011", "grid-props": "KOBO-011",
  position: "KOBO-012", transform: "KOBO-013", animation: "KOBO-014",
  "object-fit": "KOBO-015", "aspect-ratio": "KOBO-016", has: "KOBO-017",
  "is-where": "KOBO-018", "writing-mode": "KOBO-019",
};

export const DEFAULT_OPTS = {
  rem: true, rootFontSize: 16, viewport: true, vwBase: 600, vhBase: 800, clampPick: "pref",
};

// --------------------------------------------------------------------------- //
// Low-level scanners
// --------------------------------------------------------------------------- //

function matchAt(re, s, i) {
  re.lastIndex = i;
  const m = re.exec(s);
  return m && m.index === i ? m : null;
}

function skipString(s, i) {
  const quote = s[i];
  i += 1;
  const n = s.length;
  while (i < n) {
    const c = s[i];
    if (c === "\\") { i += 2; continue; }
    if (c === quote) return i + 1;
    i += 1;
  }
  return n;
}

export function findMatchingParen(s, i) {
  const n = s.length;
  let depth = 0;
  while (i < n) {
    const c = s[i];
    if (c === "/" && i + 1 < n && s[i + 1] === "*") {
      const j = s.indexOf("*/", i + 2);
      i = j === -1 ? n : j + 2;
      continue;
    }
    if (c === '"' || c === "'") { i = skipString(s, i); continue; }
    if (c === "(") depth += 1;
    else if (c === ")") { depth -= 1; if (depth === 0) return i; }
    i += 1;
  }
  return -1;
}

export function splitTopLevel(s, sep = ",") {
  const parts = [];
  let depth = 0, i = 0, start = 0;
  const n = s.length;
  while (i < n) {
    const c = s[i];
    if (c === "/" && i + 1 < n && s[i + 1] === "*") {
      const j = s.indexOf("*/", i + 2);
      i = j === -1 ? n : j + 2;
      continue;
    }
    if (c === '"' || c === "'") { i = skipString(s, i); continue; }
    if (c === "(") depth += 1;
    else if (c === ")") depth -= 1;
    else if (c === sep && depth === 0) { parts.push(s.slice(start, i)); start = i + 1; }
    i += 1;
  }
  parts.push(s.slice(start));
  return parts;
}

// Yields {code:boolean, text, start}. Comments, quoted strings AND unquoted
// url(...) are emitted as non-code so value passes never touch them.
export function splitCodeSegments(s) {
  const out = [];
  const n = s.length;
  let i = 0, codeStart = 0;
  while (i < n) {
    const c = s[i];
    if (c === "/" && i + 1 < n && s[i + 1] === "*") {
      if (i > codeStart) out.push({ code: true, text: s.slice(codeStart, i), start: codeStart });
      let j = s.indexOf("*/", i + 2);
      j = j === -1 ? n : j + 2;
      out.push({ code: false, text: s.slice(i, j), start: i });
      i = j; codeStart = i; continue;
    }
    if (c === '"' || c === "'") {
      if (i > codeStart) out.push({ code: true, text: s.slice(codeStart, i), start: codeStart });
      const j = skipString(s, i);
      out.push({ code: false, text: s.slice(i, j), start: i });
      i = j; codeStart = i; continue;
    }
    if ((c === "u" || c === "U") && matchAt(URL_OPEN_RE, s, i)) {
      if (i > codeStart) out.push({ code: true, text: s.slice(codeStart, i), start: codeStart });
      let e = findMatchingParen(s, i + 3);
      e = e === -1 ? n : e + 1;
      out.push({ code: false, text: s.slice(i, e), start: i });
      i = e; codeStart = i; continue;
    }
    i += 1;
  }
  if (codeStart < n) out.push({ code: true, text: s.slice(codeStart, n), start: codeStart });
  return out;
}

// Run a replacer only on code regions. replFn receives a RegExpMatchArray.
function subInCode(reGlobal, replFn, s) {
  let count = 0;
  const out = [];
  for (const seg of splitCodeSegments(s)) {
    if (!seg.code) { out.push(seg.text); continue; }
    out.push(seg.text.replace(reGlobal, (...args) => {
      count += 1;
      // args: match, ...groups, offset, string
      return replFn(args);
    }));
  }
  return [out.join(""), count];
}

// --------------------------------------------------------------------------- //
// Custom properties
// --------------------------------------------------------------------------- //

export function collectCustomProperties(css, varmap) {
  for (const seg of splitCodeSegments(css)) {
    if (!seg.code) continue;
    const text = seg.text;
    DECL_VAR_RE.lastIndex = 0;
    let m;
    while ((m = DECL_VAR_RE.exec(text)) !== null) {
      const name = m[1];
      let j = DECL_VAR_RE.lastIndex;
      const n = text.length;
      let depth = 0;
      while (j < n) {
        const ch = text[j];
        if (ch === "(") depth += 1;
        else if (ch === ")") depth -= 1;
        else if ((ch === ";" || ch === "}") && depth <= 0) break;
        j += 1;
      }
      varmap.set(name, text.slice(DECL_VAR_RE.lastIndex, j).trim());
    }
  }
}

export function expandVars(s, varmap, stack = []) {
  const out = [];
  let i = 0;
  const n = s.length;
  while (i < n) {
    const c = s[i];
    if (c === "/" && i + 1 < n && s[i + 1] === "*") {
      let j = s.indexOf("*/", i + 2);
      j = j === -1 ? n : j + 2;
      out.push(s.slice(i, j)); i = j; continue;
    }
    if (c === '"' || c === "'") { const j = skipString(s, i); out.push(s.slice(i, j)); i = j; continue; }
    const m = matchAt(IDENT_RE, s, i);
    if (m) {
      const ident = m[0];
      const k = i + ident.length;
      const low = ident.toLowerCase();
      if (low === "url" && k < n && s[k] === "(") {
        let e = findMatchingParen(s, k); e = e === -1 ? n : e + 1;
        out.push(s.slice(i, e)); i = e; continue;
      }
      if (low === "var" && k < n && s[k] === "(") {
        const e = findMatchingParen(s, k);
        if (e === -1) { out.push(s.slice(i)); return out.join(""); }
        const inner = s.slice(k + 1, e);
        const parts = splitTopLevel(inner, ",");
        const name = parts[0].trim();
        const fallback = parts.length > 1 ? parts.slice(1).join(",").trim() : null;
        let repl;
        if (stack.includes(name)) {
          repl = fallback !== null ? fallback : "";
        } else {
          const val = varmap.get(name);
          if (val !== undefined) repl = expandVars(val, varmap, [...stack, name]);
          else if (fallback !== null) repl = expandVars(fallback, varmap, [...stack, name]);
          else repl = "";
        }
        out.push(repl); i = e + 1; continue;
      }
      out.push(ident); i = k; continue;
    }
    out.push(c); i += 1;
  }
  return out.join("");
}

export function stripVarDeclarations(css) {
  const out = [];
  let removed = 0;
  for (const seg of splitCodeSegments(css)) {
    if (!seg.code) { out.push(seg.text); continue; }
    const text = seg.text;
    const res = [];
    let i = 0;
    const n = text.length;
    while (i < n) {
      const m = matchAt(/(--[A-Za-z0-9_-]+)\s*:/y, text, i);
      if (m) {
        const prev = text.slice(0, i).replace(/\s+$/, "");
        if (prev === "" || "{;}".includes(prev[prev.length - 1])) {
          let depth = 0;
          let j = i + m[0].length;
          while (j < n) {
            const ch = text[j];
            if (ch === "(") depth += 1;
            else if (ch === ")") depth -= 1;
            else if (ch === ";" && depth <= 0) { j += 1; break; }
            else if (ch === "}" && depth <= 0) break;
            j += 1;
          }
          removed += 1;
          i = j;
          while (i < n && (text[i] === " " || text[i] === "\t")) i += 1;
          continue;
        }
      }
      res.push(text[i]); i += 1;
    }
    out.push(res.join(""));
  }
  return [out.join(""), removed];
}

// --------------------------------------------------------------------------- //
// Value functions
// --------------------------------------------------------------------------- //

export function parseLength(tok) {
  tok = tok.trim();
  const m = NUM_SPLIT_RE.exec(tok);
  if (!m) return null;
  return [parseFloat(m[1]), m[2].toLowerCase()];
}

export function fmtNum(x) {
  if (Math.abs(x - Math.round(x)) < 1e-9) return String(Math.round(x));
  return x.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}

export function toPx(token, opts) {
  const pl = parseLength(token);
  if (!pl) return null;
  const [num, unit] = pl;
  if (unit in ABSOLUTE_PX) return num * ABSOLUTE_PX[unit];
  if (unit === "rem") return num * opts.rootFontSize;
  if (unit === "vw") return (num / 100.0) * opts.vwBase;
  if (unit === "vh") return (num / 100.0) * opts.vhBase;
  if (unit === "vmin") return (num / 100.0) * Math.min(opts.vwBase, opts.vhBase);
  if (unit === "vmax") return (num / 100.0) * Math.max(opts.vwBase, opts.vhBase);
  return null;
}

export function pickMinMax(args, which, opts) {
  args = args.map((a) => a.trim()).filter((a) => a);
  if (!args.length) return "0";
  const resolvable = args.map((a) => [toPx(a, opts), a]).filter((t) => t[0] !== null);
  if (resolvable.length) {
    resolvable.sort((a, b) => a[0] - b[0]);
    const chosen = which === "min" ? resolvable[0] : resolvable[resolvable.length - 1];
    return fmtNum(chosen[0]) + "px";
  }
  const parsed = args.map((a) => [parseLength(a), a]);
  const units = new Set(parsed.filter((t) => t[0]).map((t) => t[0][1]));
  if (units.size === 1 && parsed.every((t) => t[0])) {
    parsed.sort((a, b) => a[0][0] - b[0][0]);
    return which === "min" ? parsed[0][1] : parsed[parsed.length - 1][1];
  }
  return args[0];
}

export function pickClamp(args, opts) {
  args = args.map((a) => a.trim()).filter((a) => a);
  if (!args.length) return "0";
  if (args.length === 1) return args[0];
  const mn = args[0];
  const pref = args.length > 1 ? args[1] : args[0];
  const mx = args.length > 2 ? args[2] : args[args.length - 1];
  const pv = toPx(mn, opts), prefv = toPx(pref, opts), mxv = toPx(mx, opts);
  if (pv !== null && prefv !== null && mxv !== null) {
    return fmtNum(Math.max(pv, Math.min(prefv, mxv))) + "px";
  }
  for (const cand of [pref, mn, mx]) {
    const pl = parseLength(cand);
    if (pl && pl[1] in ABSOLUTE_PX) return cand;
  }
  const choice = opts.clampPick || "pref";
  const cand = { min: mn, pref, max: mx }[choice] ?? pref;
  const v = toPx(cand, opts);
  return v !== null ? fmtNum(v) + "px" : cand;
}

// ---- calc() evaluator ----
class CalcError extends Error {}

function calcTokens(s) {
  const toks = [];
  let i = 0;
  const n = s.length;
  while (i < n) {
    const c = s[i];
    if (/\s/.test(c)) { i += 1; continue; }
    if (c === "(" || c === ")") { toks.push(["par", c]); i += 1; continue; }
    if ("+-*/".includes(c)) { toks.push(["op", c]); i += 1; continue; }
    const m = matchAt(NUMUNIT_RE, s, i);
    if (m) { toks.push(["num", m[0]]); i += m[0].length; continue; }
    throw new CalcError("bad token " + s.slice(i));
  }
  return toks;
}

function vNum(numstr) {
  const m = NUM_SPLIT_RE.exec(numstr);
  if (!m) throw new CalcError("bad number " + numstr);
  return { [m[2].toLowerCase()]: parseFloat(m[1]) };
}
function vAddSub(a, b, sign) {
  const out = { ...a };
  for (const [u, c] of Object.entries(b)) out[u] = (out[u] || 0) + sign * c;
  return out;
}
function isUnitless(v) {
  const keys = Object.entries(v).filter(([, c]) => Math.abs(c) > 1e-12).map(([u]) => u);
  return keys.length === 0 || (keys.length === 1 && keys[0] === "");
}
function scalar(v) { return v[""] || 0; }
function vMul(a, b) {
  if (isUnitless(a)) { const s = scalar(a); const o = {}; for (const [u, c] of Object.entries(b)) o[u] = c * s; return o; }
  if (isUnitless(b)) { const s = scalar(b); const o = {}; for (const [u, c] of Object.entries(a)) o[u] = c * s; return o; }
  throw new CalcError("cannot multiply two dimensioned values");
}
function vDiv(a, b) {
  if (!isUnitless(b)) throw new CalcError("cannot divide by a dimensioned value");
  const d = scalar(b);
  if (Math.abs(d) < 1e-12) throw new CalcError("division by zero");
  const o = {}; for (const [u, c] of Object.entries(a)) o[u] = c / d; return o;
}

class CalcParser {
  constructor(toks) { this.toks = toks; this.i = 0; }
  peek() { return this.i < this.toks.length ? this.toks[this.i] : [null, null]; }
  next() { return this.toks[this.i++]; }
  parse() { const v = this.expr(); if (this.i !== this.toks.length) throw new CalcError("trailing tokens"); return v; }
  expr() {
    let v = this.term();
    while (this.peek()[0] === "op" && "+-".includes(this.peek()[1])) {
      const op = this.next()[1]; const rhs = this.term();
      v = vAddSub(v, rhs, op === "+" ? 1 : -1);
    }
    return v;
  }
  term() {
    let v = this.factor();
    while (this.peek()[0] === "op" && "*/".includes(this.peek()[1])) {
      const op = this.next()[1]; const rhs = this.factor();
      v = op === "*" ? vMul(v, rhs) : vDiv(v, rhs);
    }
    return v;
  }
  factor() {
    let t = this.peek();
    let sign = 1;
    while (t[0] === "op" && "+-".includes(t[1])) { this.next(); if (t[1] === "-") sign *= -1; t = this.peek(); }
    let v;
    if (t[0] === "par" && t[1] === "(") {
      this.next(); v = this.expr();
      const close = this.peek();
      if (!(close[0] === "par" && close[1] === ")")) throw new CalcError("missing )");
      this.next();
    } else if (t[0] === "num") {
      v = vNum(this.next()[1]);
    } else {
      throw new CalcError("unexpected " + JSON.stringify(t));
    }
    if (sign === -1) { const o = {}; for (const [u, c] of Object.entries(v)) o[u] = -c; v = o; }
    return v;
  }
}

function calcEval(inner) {
  const v = new CalcParser(calcTokens(inner)).parse();
  const nz = Object.entries(v).filter(([, c]) => Math.abs(c) > 1e-9);
  if (nz.length === 0) return "0";
  if (nz.length > 1) throw new CalcError("mixed units");
  const [unit, coeff] = nz[0];
  return fmtNum(coeff) + unit;
}

function calcFallback(inner) {
  let depth = 0;
  const n = inner.length;
  let i = 0;
  while (i < n && (inner[i] === " " || inner[i] === "\t")) i += 1;
  const start = i;
  while (i < n) {
    const c = inner[i];
    if (c === "(") depth += 1;
    else if (c === ")") depth -= 1;
    else if ("+-".includes(c) && depth === 0 && i > start) {
      let j = i - 1;
      while (j >= start && (inner[j] === " " || inner[j] === "\t")) j -= 1;
      if (j >= start && !"eE".includes(inner[j]) && (/[A-Za-z0-9]/.test(inner[j]) || "%)".includes(inner[j]))) break;
    }
    i += 1;
  }
  const term = inner.slice(start, i).trim();
  return term || inner.trim();
}

function evaluateFunc(name, inner, opts, source, changes, orig) {
  let repl, note = "";
  if (name === "min" || name === "max") {
    repl = pickMinMax(splitTopLevel(inner, ","), name, opts);
    note = "kept the static term; the fluid term was dropped";
  } else if (name === "clamp") {
    repl = pickClamp(splitTopLevel(inner, ","), opts);
    note = "reduced to a single static size (lossy)";
  } else if (name === "calc") {
    try { repl = calcEval(inner); }
    catch (e) { repl = calcFallback(inner); note = "mixed/relative units: kept the first term (approximate)"; }
  } else if (name === "env") {
    const args = splitTopLevel(inner, ",");
    repl = args.length > 1 ? args[1].trim() : "0";
    note = "env() not supported; used its fallback";
  } else {
    return orig;
  }
  changes.push({ source, type: name, from: orig.trim(), to: repl, note });
  return repl;
}

export function evalFuncs(s, opts, changes, source) {
  const out = [];
  let i = 0;
  const n = s.length;
  while (i < n) {
    const c = s[i];
    if (c === "/" && i + 1 < n && s[i + 1] === "*") {
      let j = s.indexOf("*/", i + 2); j = j === -1 ? n : j + 2;
      out.push(s.slice(i, j)); i = j; continue;
    }
    if (c === '"' || c === "'") { const j = skipString(s, i); out.push(s.slice(i, j)); i = j; continue; }
    const m = matchAt(IDENT_RE, s, i);
    if (m) {
      const ident = m[0];
      const k = i + ident.length;
      const low = ident.toLowerCase();
      const base = low.replace(VENDOR_RE, "");
      if (k < n && s[k] === "(" && (base === "url" || MATH_FUNCS.has(base))) {
        const e = findMatchingParen(s, k);
        if (e === -1) { out.push(s.slice(i)); return out.join(""); }
        if (base === "url") { out.push(s.slice(i, e + 1)); i = e + 1; continue; }
        const orig = s.slice(i, e + 1);
        const inner = evalFuncs(s.slice(k + 1, e), opts, changes, source);
        out.push(evaluateFunc(base, inner, opts, source, changes, orig));
        i = e + 1; continue;
      }
      out.push(ident); i = k; continue;
    }
    out.push(c); i += 1;
  }
  return out.join("");
}

// --------------------------------------------------------------------------- //
// rem / viewport / empty-rule / cleanup
// --------------------------------------------------------------------------- //

export function convertRem(css, opts, changes, source) {
  if (!opts.rem) return css;
  const root = opts.rootFontSize;
  const [out] = subInCode(REM_RE, (args) => {
    const px = parseFloat(args[1]) * root;
    const neu = fmtNum(px) + "px";
    changes.push({ source, type: "rem", from: args[0], to: neu,
      note: "RMSDK treats rem as em; converted to a fixed px" });
    return neu;
  }, css);
  return out;
}

export function convertViewport(css, opts, changes, source) {
  if (!opts.viewport) return css;
  const [out] = subInCode(VPUNIT_RE, (args) => {
    const num = parseFloat(args[1]);
    const unit = args[2].toLowerCase();
    const base = { vw: opts.vwBase, vh: opts.vhBase,
      vmin: Math.min(opts.vwBase, opts.vhBase), vmax: Math.max(opts.vwBase, opts.vhBase) }[unit];
    const neu = fmtNum((num / 100.0) * base) + "px";
    changes.push({ source, type: "viewport", from: args[0], to: neu,
      note: `viewport units are unreliable/crashing on RMSDK; converted against an assumed ${opts.vwBase}x${opts.vhBase} px viewport` });
    return neu;
  }, css);
  return out;
}

export function stripEmptyAtrules(css, changes, source) {
  let totalAt = 0;
  for (;;) {
    let na = 0, nr = 0;
    [css, na] = subInCode(EMPTY_ATRULE_RE, () => "", css);
    [css, nr] = subInCode(EMPTY_RULESET_RE, () => "", css);
    totalAt += na;
    if (na === 0 && nr === 0) break;
  }
  if (totalAt) {
    changes.push({ source, type: "empty-atrule", from: "", to: "",
      note: `removed ${totalAt} empty @media/@supports block(s) (crashes old RMSDK)` });
  }
  return css;
}

export function cleanupEmptyDecls(css) {
  const [out] = subInCode(EMPTY_DECL_RE, () => "", css);
  return out;
}

function lineOf(full, pos) {
  let count = 1;
  for (let k = 0; k < pos; k++) if (full[k] === "\n") count++;
  return count;
}

export function detectReportOnly(css, source, warnings) {
  const seen = new Set();
  for (const seg of splitCodeSegments(css)) {
    if (!seg.code) continue;
    for (const [key, rx, msg] of REPORT_ONLY) {
      rx.lastIndex = 0;
      let m;
      while ((m = rx.exec(seg.text)) !== null) {
        const line = lineOf(css, seg.start + m.index);
        const dedup = key + ":" + line;
        if (seen.has(dedup)) continue;
        seen.add(dedup);
        warnings.push({ source, key, line, snippet: m[0].trim(), message: msg });
        if (m.index === rx.lastIndex) rx.lastIndex++;
      }
    }
  }
}

// --------------------------------------------------------------------------- //
// Top-level CSS / markup
// --------------------------------------------------------------------------- //

export function sanitizeCssText(css, varmap, opts, changes, warnings, source, isStylesheet) {
  detectReportOnly(css, source, warnings);
  css = expandVars(css, varmap);
  if (isStylesheet) css = stripVarDeclarations(css)[0];
  css = evalFuncs(css, opts, changes, source);
  css = convertRem(css, opts, changes, source);
  css = convertViewport(css, opts, changes, source);
  css = cleanupEmptyDecls(css);
  if (isStylesheet) css = stripEmptyAtrules(css, changes, source);
  return css;
}

function applyOutsideSkips(text, fn) {
  const out = [];
  let last = 0;
  MARKUP_SKIP_RE.lastIndex = 0;
  let m;
  while ((m = MARKUP_SKIP_RE.exec(text)) !== null) {
    out.push(fn(text.slice(last, m.index)));
    out.push(m[0]);
    last = m.index + m[0].length;
    if (m.index === MARKUP_SKIP_RE.lastIndex) MARKUP_SKIP_RE.lastIndex++;
  }
  out.push(fn(text.slice(last)));
  return out.join("");
}

export function processXhtml(text, varmap, opts, changes, warnings, source) {
  text = text.replace(STYLE_BLOCK_RE, (mm, open, inner, close) =>
    open + sanitizeCssText(inner, varmap, opts, changes, warnings, source + " <style>", true) + close);
  return applyOutsideSkips(text, (seg) =>
    seg.replace(STYLE_ATTR_RE, (mm, pre, q, inner, q2) =>
      pre + q + sanitizeCssText(inner, varmap, opts, changes, warnings, source + ' style="..."', false) + q2));
}

export function collectXhtmlVars(text, varmap) {
  for (const m of text.matchAll(STYLE_BLOCK_RE)) collectCustomProperties(m[2], varmap);
  applyOutsideSkips(text, (seg) => {
    for (const m of seg.matchAll(STYLE_ATTR_RE)) collectCustomProperties(m[3], varmap);
    return seg;
  });
}

export function retagToUtf8(text, isCss) {
  if (isCss) {
    const m = CSS_CHARSET_RE.exec(text);
    if (m && !["utf-8", "us-ascii"].includes(m[1].trim().toLowerCase())) {
      return text.replace(CSS_CHARSET_RE, '@charset "utf-8";');
    }
    return text;
  }
  const m = XML_ENC_RE.exec(text);
  if (m && !["utf-8", "us-ascii"].includes(m[2].trim().toLowerCase())) {
    return text.replace(XML_ENC_RE, (mm, a, _v, b) => a + "utf-8" + b);
  }
  return text;
}

// --------------------------------------------------------------------------- //
// Lint
// --------------------------------------------------------------------------- //

export function lintFragment(frag, base, fullText, source, findings) {
  for (const seg of splitCodeSegments(frag)) {
    if (!seg.code) continue;
    const off = base + seg.start;
    for (const m of seg.text.matchAll(FUNC_LINT_RE)) {
      const fn = m[1].toLowerCase();
      findings.push({ engine: "kobofix", rule: "KOBO-001", severity: "error", source,
        line: lineOf(fullText, off + m.index), snippet: m[0].trim(),
        message: `CSS function ${fn}() - legacy RMSDK can't parse it and drops the ENTIRE stylesheet; the book may render unstyled or refuse to open ('corrupted'). Remove/resolve it.`,
        fixable: true });
    }
    for (const m of seg.text.matchAll(VPUNIT_RE)) {
      findings.push({ engine: "kobofix", rule: "KOBO-002", severity: "warning", source,
        line: lineOf(fullText, off + m.index), snippet: m[0].trim(),
        message: `Viewport unit '${m[0].trim()}' - unreliable on RMSDK (no real viewport); in a margin it can crash Kobo to a blank screen.`,
        fixable: true });
    }
    for (const m of seg.text.matchAll(REM_RE)) {
      findings.push({ engine: "kobofix", rule: "KOBO-004", severity: "warning", source,
        line: lineOf(fullText, off + m.index), snippet: m[0].trim(),
        message: "rem unit - legacy RMSDK renders rem as em, so sizes compound through inheritance and come out wrong.",
        fixable: true });
    }
    for (const [key, rx, msg] of REPORT_ONLY) {
      for (const m of seg.text.matchAll(rx)) {
        findings.push({ engine: "kobofix", rule: LAYOUT_RULE[key] || "KOBO-019",
          severity: key === "writing-mode" ? "info" : "warning", source,
          line: lineOf(fullText, off + m.index), snippet: m[0].trim(), message: msg, fixable: false });
      }
    }
  }
  for (const m of frag.matchAll(EMPTY_ATRULE_RE)) {
    findings.push({ engine: "kobofix", rule: "KOBO-003", severity: "warning", source,
      line: lineOf(fullText, base + m.index),
      snippet: m[0].split(/\s+/).join(" ").slice(0, 40),
      message: "Empty @media/@supports block - can crash older RMSDK.", fixable: true });
  }
}

export function lintMarkup(text, source, findings) {
  const skipSpans = [];
  for (const m of text.matchAll(MARKUP_SKIP_RE)) skipSpans.push([m.index, m.index + m[0].length]);
  const inSkip = (pos) => skipSpans.some(([a, b]) => a <= pos && pos < b);
  for (const m of text.matchAll(STYLE_BLOCK_RE)) {
    const idx = m.index + m[1].length;
    lintFragment(m[2], idx, text, source + " <style>", findings);
  }
  for (const m of text.matchAll(STYLE_ATTR_RE)) {
    if (inSkip(m.index)) continue;
    const idx = m.index + m[1].length + m[2].length;
    lintFragment(m[3], idx, text, source + ' style=""', findings);
  }
}
