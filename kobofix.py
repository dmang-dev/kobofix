#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kobofix - make an EPUB safe for Kobo e-readers (Adobe RMSDK rendering engine).

WHY THIS EXISTS
---------------
Kobo e-readers render sideloaded .epub files through Adobe's legacy "RMSDK"
engine, whose CSS parser is frozen around 2013. Two things make it dangerous:

  1. NO CSS FAULT TOLERANCE. When RMSDK meets a value-function token it cannot
     parse -- calc(), min(), max(), clamp(), var(), env() -- it does NOT skip
     that one declaration the way a browser does. It throws away the ENTIRE
     stylesheet, and on some firmware refuses to open the book at all
     ("this book is corrupted"). A CSS fallback declaration placed *before*
     the modern one is therefore useless: the whole sheet (fallback included)
     is discarded. The construct must be physically REMOVED / resolved.

  2. "CORRUPTED" IS OFTEN A PACKAGING BUG. The most common literal-"corrupted"
     cause is the EPUB's `mimetype` zip entry not being the first entry, or
     being compressed, or carrying an extra field. So this tool always re-emits
     a spec-correct OCF ZIP regardless of whether any CSS changed.

WHAT IT DOES
------------
Reads an .epub (or an already-extracted folder), then:

  * Resolves CSS custom properties: every var(--x[,fallback]) is replaced with
    its literal value and the `--x:` declarations are deleted.
  * Eliminates math/value functions: calc()/min()/max()/clamp()/env() are
    evaluated or reduced to a single static value (no token left behind).
  * Converts `rem` -> px (RMSDK mis-treats rem as em, compounding sizes).
  * Converts standalone viewport units vw/vh/vmin/vmax -> px against an assumed
    reader viewport (verified crash: `margin:50vh` blanks a Kobo screen).
  * Strips empty @media/@supports blocks (verified crash on old RMSDK).
  * Applies all of the above inside .css files AND inside <style> blocks and
    style="..." attributes in XHTML.
  * REPORTS (never silently rewrites) layout features that have no safe
    automatic equivalent: flexbox, grid, transforms, position:absolute/fixed,
    :has(), object-fit, etc. -- you decide what to do with those.
  * Repackages a valid OCF ZIP: mimetype first, STORED, no extra field, exact
    bytes; everything else DEFLATE; original paths and UTF-8 (no BOM) preserved.
  * Verifies its own output and (optionally) runs EPUBCheck.

Pure standard library. No pip install required.

Sources behind the rules (all verified during research):
  - andreklein.net "Your EPUB is fine, Kobo disagrees - blame Adobe"
  - dvschultz/99problems#53 "Legacy RMSDK will ignore the entire stylesheet if you use calc()"
  - Jiminy Panoz, "Five interesting facts about Adobe legacy eBook RMSDK"
  - Readium CSS docs (CSS21-epub_compat, CSS07-variables)
  - J-Novel Club forum "Blank epub on Kobo?" (margin:50vh crash)
  - EPUB OCF 3.x spec + W3C EPUB 3.3 (mimetype packaging rules)
"""

import argparse
import glob
import io
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

VERSION = "1.0.0"

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

MATH_FUNCS = ("calc", "min", "max", "clamp", "env")

# Absolute length units and their size in px (CSS reference pixel).
ABSOLUTE_PX = {
    "px": 1.0,
    "pt": 96.0 / 72.0,
    "pc": 16.0,
    "in": 96.0,
    "cm": 96.0 / 2.54,
    "mm": 96.0 / 25.4,
    "q": 96.0 / 25.4 / 4.0,  # 1q = 1/40 cm
}
# Relative units we will not try to compare numerically (no viewport / no box).
VIEWPORT_UNITS = ("vw", "vh", "vmin", "vmax")

IDENT_RE = re.compile(r"-?[A-Za-z_][A-Za-z0-9_-]*")
NUMUNIT_RE = re.compile(r"\d*\.?\d+(?:[eE][+-]?\d+)?[A-Za-z%]*")
NUM_SPLIT_RE = re.compile(r"^([+-]?\d*\.?\d+(?:[eE][+-]?\d+)?)([A-Za-z%]*)$")
# Leading CSS vendor prefix, e.g. -webkit-calc(), -moz-min(), -ms-clamp().
VENDOR_RE = re.compile(r"^-(?:webkit|moz|o|ms)-")
# Start of a url(...) token, which is opaque to value-rewriting passes.
URL_OPEN_RE = re.compile(r"url\(", re.I)
# In-document encoding declarations we normalise to UTF-8 when re-emitting.
XML_ENC_RE = re.compile(r"(<\?xml\b[^>]*?\bencoding\s*=\s*[\"'])([^\"']*)([\"'])", re.I)
CSS_CHARSET_RE = re.compile(r'^@charset\s+"([^"]*)";', re.I)

# Report-only patterns: (key, compiled regex, human message). These are
# detected and reported but never auto-rewritten -- there is no safe mechanical
# equivalent, so a human decides.
REPORT_ONLY = [
    ("flexbox", re.compile(r"display\s*:\s*(?:inline-)?flex", re.I),
     "Flexbox: RMSDK ignores it; flex children fall back to block flow. "
     "Side-by-side/justify/order layouts will break. Rework with float/table or wrap in @supports."),
    ("grid", re.compile(r"display\s*:\s*(?:inline-)?grid", re.I),
     "CSS Grid: unsupported by RMSDK; tracks/areas are lost and items stack. No mechanical fallback."),
    ("grid-props", re.compile(r"\bgrid-(?:template|area|auto|column|row)\b", re.I),
     "Grid sub-property: only meaningful with display:grid, which RMSDK ignores."),
    ("position", re.compile(r"position\s*:\s*(?:sticky|fixed|absolute)", re.I),
     "position:absolute/fixed/sticky in reflowable EPUB is unreliable on RMSDK and can push content off-page."),
    ("transform", re.compile(r"(?<![\w-])transform\s*:", re.I),
     "CSS transform is ignored by RMSDK (no rotation/scale). Decorative-only is harmless; load-bearing breaks."),
    ("animation", re.compile(r"@keyframes|\banimation\s*:|\btransition\s*:", re.I),
     "Animations/transitions are dropped on the eInk/RMSDK path. Ensure the resting style is the final frame."),
    ("object-fit", re.compile(r"\bobject-fit\s*:", re.I),
     "object-fit is unsupported by RMSDK; images stretch to declared width/height instead of cropping."),
    ("aspect-ratio", re.compile(r"\baspect-ratio\s*:", re.I),
     "aspect-ratio is unknown to RMSDK (silently dropped). Provide explicit width/height if the ratio matters."),
    ("has", re.compile(r":has\(", re.I),
     ":has() has no equivalent on RMSDK and the whole rule may be dropped. Rewrite by hand."),
    ("is-where", re.compile(r":(?:is|where)\(", re.I),
     ":is()/:where() may not parse on RMSDK. Expand to a comma-separated selector list by hand."),
    ("writing-mode", re.compile(r"\bwriting-mode\s*:", re.I),
     "writing-mode: Kobo DOES support this for vertical CJK text -- leave it alone unless you know you don't need it."),
]


# --------------------------------------------------------------------------- #
# Low-level scanning helpers (comment/string aware)
# --------------------------------------------------------------------------- #

def skip_string(s, i):
    """s[i] is a quote. Return index just past the closing quote."""
    quote = s[i]
    i += 1
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return i + 1
        i += 1
    return n


def find_matching_paren(s, i):
    """s[i] == '('. Return index of the matching ')', or -1 if unbalanced.
    Respects nested parens, strings and /* comments */."""
    n = len(s)
    depth = 0
    while i < n:
        c = s[i]
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            j = s.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c == '"' or c == "'":
            i = skip_string(s, i)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def split_top_level(s, sep=","):
    """Split s on `sep` that appears at paren-depth 0, ignoring commas inside
    nested parens, strings and comments."""
    parts = []
    depth = 0
    n = len(s)
    i = 0
    start = 0
    while i < n:
        c = s[i]
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            j = s.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c == '"' or c == "'":
            i = skip_string(s, i)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == sep and depth == 0:
            parts.append(s[start:i])
            start = i + 1
        i += 1
    parts.append(s[start:])
    return parts


def split_code_segments(s):
    """Yield (is_code, text, start_offset) segments, separating CSS code from
    /* comments */ and "string" / 'string' literals so regex passes never touch
    comments or strings."""
    n = len(s)
    i = 0
    code_start = 0
    while i < n:
        c = s[i]
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            if i > code_start:
                yield (True, s[code_start:i], code_start)
            j = s.find("*/", i + 2)
            j = n if j == -1 else j + 2
            yield (False, s[i:j], i)
            i = j
            code_start = i
            continue
        if c == '"' or c == "'":
            if i > code_start:
                yield (True, s[code_start:i], code_start)
            j = skip_string(s, i)
            yield (False, s[i:j], i)
            i = j
            code_start = i
            continue
        if (c == "u" or c == "U") and URL_OPEN_RE.match(s, i):
            # url(...) — including the unquoted form — is opaque: a filename like
            # url(2rem-icon.png) must never be touched by the rem/viewport passes.
            if i > code_start:
                yield (True, s[code_start:i], code_start)
            e = find_matching_paren(s, i + 3)  # s[i+3] == '('
            e = n if e == -1 else e + 1
            yield (False, s[i:e], i)
            i = e
            code_start = i
            continue
        i += 1
    if code_start < n:
        yield (True, s[code_start:n], code_start)


def sub_in_code(pattern, repl_fn, s):
    """Run re.sub(pattern, repl_fn) only on the code regions of s, leaving
    comments/strings untouched. Returns (new_string, num_substitutions)."""
    out = []
    count = 0

    def wrap(m):
        nonlocal count
        count += 1
        return repl_fn(m)

    for is_code, text, _start in split_code_segments(s):
        out.append(pattern.sub(wrap, text) if is_code else text)
    return "".join(out), count


# --------------------------------------------------------------------------- #
# Custom properties: collect, resolve var(), strip declarations
# --------------------------------------------------------------------------- #

DECL_VAR_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:")


def collect_custom_properties(css, varmap):
    """Find `--name: value` declarations and add them to varmap (last wins).
    Scans only code regions; value runs to the next top-level ; or }."""
    for is_code, text, _ in split_code_segments(css):
        if not is_code:
            continue
        for m in DECL_VAR_RE.finditer(text):
            name = m.group(1)
            vstart = m.end()
            # Read value until top-level ';' or '}'.
            depth = 0
            j = vstart
            n = len(text)
            while j < n:
                ch = text[j]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                elif (ch == ";" or ch == "}") and depth <= 0:
                    break
                j += 1
            value = text[vstart:j].strip()
            varmap[name] = value


def expand_vars(s, varmap, stack=()):
    """Replace every var(--name[,fallback]) with its resolved literal value.
    Recurses into resolved values so nested var()s expand too; guards cycles."""
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            j = s.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(s[i:j])
            i = j
            continue
        if c == '"' or c == "'":
            j = skip_string(s, i)
            out.append(s[i:j])
            i = j
            continue
        m = IDENT_RE.match(s, i)
        if m:
            ident = m.group(0)
            k = m.end()
            low = ident.lower()
            if low == "url" and k < n and s[k] == "(":
                e = find_matching_paren(s, k)
                e = n if e == -1 else e + 1
                out.append(s[i:e])
                i = e
                continue
            if low == "var" and k < n and s[k] == "(":
                e = find_matching_paren(s, k)
                if e == -1:
                    out.append(s[i:])
                    return "".join(out)
                inner = s[k + 1:e]
                parts = split_top_level(inner, ",")
                name = parts[0].strip()
                fallback = ",".join(parts[1:]).strip() if len(parts) > 1 else None
                if name in stack:  # cycle
                    repl = fallback if fallback is not None else ""
                else:
                    val = varmap.get(name)
                    if val is not None:
                        repl = expand_vars(val, varmap, stack + (name,))
                    elif fallback is not None:
                        repl = expand_vars(fallback, varmap, stack + (name,))
                    else:
                        repl = ""
                out.append(repl)
                i = e + 1
                continue
            out.append(ident)
            i = k
            continue
        out.append(c)
        i += 1
    return "".join(out)


def strip_var_declarations(css):
    """Remove `--name: value;` custom-property declarations from code regions."""
    out = []
    removed = 0
    for is_code, text, _ in split_code_segments(css):
        if not is_code:
            out.append(text)
            continue
        res = []
        i = 0
        n = len(text)
        while i < n:
            m = DECL_VAR_RE.match(text, i)
            # Only treat as a declaration start if preceded by { ; or start.
            if m:
                prev = text[:i].rstrip()
                if prev == "" or prev[-1] in "{;}":
                    depth = 0
                    j = m.end()
                    while j < n:
                        ch = text[j]
                        if ch == "(":
                            depth += 1
                        elif ch == ")":
                            depth -= 1
                        elif ch == ";" and depth <= 0:
                            j += 1
                            break
                        elif ch == "}" and depth <= 0:
                            break
                        j += 1
                    removed += 1
                    i = j
                    # also swallow following whitespace/newline
                    while i < n and text[i] in " \t":
                        i += 1
                    continue
            res.append(text[i])
            i += 1
        out.append("".join(res))
    return "".join(out), removed


# --------------------------------------------------------------------------- #
# Math/value function evaluation
# --------------------------------------------------------------------------- #

def parse_length(tok):
    """'150px' -> (150.0, 'px'). Returns None if not a plain number+unit."""
    tok = tok.strip()
    m = NUM_SPLIT_RE.match(tok)
    if not m:
        return None
    return (float(m.group(1)), m.group(2).lower())


def fmt_num(x):
    """Format a float as a compact CSS number."""
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return ("%.4f" % x).rstrip("0").rstrip(".")


def to_px(token, opts):
    """Resolve a length token to a px number when possible: absolute units, rem
    (via root font size), and viewport units (via the assumed bases). Returns
    None for context-dependent units (%, em, ex, ch) we can't resolve at build
    time."""
    pl = parse_length(token)
    if not pl:
        return None
    num, unit = pl
    if unit in ABSOLUTE_PX:
        return num * ABSOLUTE_PX[unit]
    if unit == "rem":
        return num * opts.root_font_size
    if unit == "vw":
        return num / 100.0 * opts.vw_base
    if unit == "vh":
        return num / 100.0 * opts.vh_base
    if unit == "vmin":
        return num / 100.0 * min(opts.vw_base, opts.vh_base)
    if unit == "vmax":
        return num / 100.0 * max(opts.vw_base, opts.vh_base)
    return None


def pick_minmax(args, which, opts):
    """Reduce min()/max() to one static term. Prefer terms resolvable to px
    (absolute, rem, viewport) and apply true min/max numerically, emitting a px
    literal. Otherwise compare same-unit relatives; else keep the first term."""
    args = [a.strip() for a in args if a.strip()]
    if not args:
        return "0"
    resolvable = [(v, a) for v, a in ((to_px(a, opts), a) for a in args) if v is not None]
    if resolvable:
        resolvable.sort(key=lambda t: t[0])
        chosen = resolvable[0] if which == "min" else resolvable[-1]
        return fmt_num(chosen[0]) + "px"
    parsed = [(parse_length(a), a) for a in args]
    units = {pl[1] for pl, _ in parsed if pl}
    if len(units) == 1 and all(pl for pl, _ in parsed):
        parsed.sort(key=lambda t: t[0][0])
        return parsed[0][1] if which == "min" else parsed[-1][1]
    return args[0]


def pick_clamp(args, opts):
    """Reduce clamp(MIN, PREF, MAX) to one static value. If every term resolves
    to px, emit the true clamped result; otherwise prefer an absolute term, then
    a px-resolved clamp_pick -- never a raw viewport term (which a later pass
    would expand past the author's bounds)."""
    args = [a.strip() for a in args if a.strip()]
    if not args:
        return "0"
    if len(args) == 1:
        return args[0]
    mn = args[0]
    pref = args[1] if len(args) > 1 else args[0]
    mx = args[2] if len(args) > 2 else args[-1]
    pv, prefv, mxv = to_px(mn, opts), to_px(pref, opts), to_px(mx, opts)
    if None not in (pv, prefv, mxv):
        return fmt_num(max(pv, min(prefv, mxv))) + "px"
    for cand in (pref, mn, mx):
        pl = parse_length(cand)
        if pl and pl[1] in ABSOLUTE_PX:
            return cand
    choice = getattr(opts, "clamp_pick", "pref")
    cand = {"min": mn, "pref": pref, "max": mx}.get(choice, pref)
    v = to_px(cand, opts)
    return fmt_num(v) + "px" if v is not None else cand


# --- calc() evaluator -------------------------------------------------------

class CalcError(Exception):
    pass


def _calc_tokens(s):
    toks = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c in "()":
            toks.append(("par", c))
            i += 1
            continue
        if c in "+-*/":
            toks.append(("op", c))
            i += 1
            continue
        m = NUMUNIT_RE.match(s, i)
        if m:
            toks.append(("num", m.group(0)))
            i = m.end()
            continue
        raise CalcError("bad token %r" % s[i:])
    return toks


def _v_num(numstr):
    m = NUM_SPLIT_RE.match(numstr)
    if not m:
        raise CalcError("bad number %r" % numstr)
    return {m.group(2).lower(): float(m.group(1))}


def _v_addsub(a, b, sign):
    out = dict(a)
    for u, c in b.items():
        out[u] = out.get(u, 0.0) + sign * c
    return out


def _unitless(v):
    keys = [u for u, c in v.items() if abs(c) > 1e-12]
    return keys == [""] or keys == []


def _scalar(v):
    return v.get("", 0.0)


def _v_mul(a, b):
    if _unitless(a):
        return {u: c * _scalar(a) for u, c in b.items()}
    if _unitless(b):
        return {u: c * _scalar(b) for u, c in a.items()}
    raise CalcError("cannot multiply two dimensioned values")


def _v_div(a, b):
    if not _unitless(b):
        raise CalcError("cannot divide by a dimensioned value")
    d = _scalar(b)
    if abs(d) < 1e-12:
        raise CalcError("division by zero")
    return {u: c / d for u, c in a.items()}


class _CalcParser:
    def __init__(self, toks):
        self.toks = toks
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def next(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def parse(self):
        v = self.expr()
        if self.i != len(self.toks):
            raise CalcError("trailing tokens")
        return v

    def expr(self):
        v = self.term()
        while self.peek()[0] == "op" and self.peek()[1] in "+-":
            op = self.next()[1]
            rhs = self.term()
            v = _v_addsub(v, rhs, 1 if op == "+" else -1)
        return v

    def term(self):
        v = self.factor()
        while self.peek()[0] == "op" and self.peek()[1] in "*/":
            op = self.next()[1]
            rhs = self.factor()
            v = _v_mul(v, rhs) if op == "*" else _v_div(v, rhs)
        return v

    def factor(self):
        t = self.peek()
        sign = 1
        while t[0] == "op" and t[1] in "+-":  # unary sign
            self.next()
            if t[1] == "-":
                sign *= -1
            t = self.peek()
        if t[0] == "par" and t[1] == "(":
            self.next()
            v = self.expr()
            if self.peek() != ("par", ")"):
                raise CalcError("missing )")
            self.next()
        elif t[0] == "num":
            v = _v_num(self.next()[1])
        else:
            raise CalcError("unexpected %r" % (t,))
        if sign == -1:
            v = {u: -c for u, c in v.items()}
        return v


def calc_eval(inner):
    """Evaluate a calc() body to a single 'value+unit' string, or raise."""
    v = _CalcParser(_calc_tokens(inner)).parse()
    nz = {u: c for u, c in v.items() if abs(c) > 1e-9}
    if len(nz) == 0:
        return "0"
    if len(nz) > 1:
        raise CalcError("mixed units: %r" % nz)
    unit, coeff = next(iter(nz.items()))
    return fmt_num(coeff) + unit


def calc_fallback(inner):
    """Mixed-unit / unparseable calc: keep the first top-level additive term."""
    # Split on top-level + or - (not the leading sign).
    depth = 0
    n = len(inner)
    i = 0
    # skip leading spaces / unary sign
    while i < n and inner[i] in " \t":
        i += 1
    start = i
    while i < n:
        c = inner[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c in "+-" and depth == 0 and i > start:
            # A binary +/- separates additive terms when the previous non-space
            # char ends a term (digit, unit letter, '%', ')'). Exclude 'e'/'E'
            # so a number's exponent (1.5e-3) is not mistaken for an operator,
            # and a leading unary sign (no term yet) is not treated as a split.
            j = i - 1
            while j >= start and inner[j] in " \t":
                j -= 1
            if j >= start and inner[j] not in "eE" and (inner[j].isalnum() or inner[j] in "%)"):
                break
        i += 1
    term = inner[start:i].strip()
    return term if term else inner.strip()


def eval_funcs(s, opts, changes, source):
    """Replace calc/min/max/clamp/env (innermost first) with static values."""
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            j = s.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(s[i:j])
            i = j
            continue
        if c == '"' or c == "'":
            j = skip_string(s, i)
            out.append(s[i:j])
            i = j
            continue
        m = IDENT_RE.match(s, i)
        if m:
            ident = m.group(0)
            k = m.end()
            low = ident.lower()
            base = VENDOR_RE.sub("", low)  # -webkit-calc -> calc, etc.
            if k < n and s[k] == "(" and (base == "url" or base in MATH_FUNCS):
                e = find_matching_paren(s, k)
                if e == -1:
                    out.append(s[i:])
                    return "".join(out)
                if base == "url":
                    out.append(s[i:e + 1])
                    i = e + 1
                    continue
                orig = s[i:e + 1]
                inner = eval_funcs(s[k + 1:e], opts, changes, source)  # nested first
                repl = evaluate_func(base, inner, opts, source, changes, orig)
                out.append(repl)
                i = e + 1
                continue
            out.append(ident)
            i = k
            continue
        out.append(c)
        i += 1
    return "".join(out)


def evaluate_func(name, inner, opts, source, changes, orig):
    note = ""
    if name in ("min", "max"):
        repl = pick_minmax(split_top_level(inner, ","), name, opts)
        note = "kept the static term; the fluid term was dropped"
    elif name == "clamp":
        repl = pick_clamp(split_top_level(inner, ","), opts)
        note = "reduced to a single static size (lossy)"
    elif name == "calc":
        try:
            repl = calc_eval(inner)
        except CalcError:
            repl = calc_fallback(inner)
            note = "mixed/relative units: kept the first term (approximate)"
    elif name == "env":
        args = split_top_level(inner, ",")
        repl = args[1].strip() if len(args) > 1 else "0"
        note = "env() not supported; used its fallback"
    else:
        return orig
    changes.append({
        "source": source, "type": name, "from": orig.strip(),
        "to": repl, "note": note,
    })
    return repl


# --------------------------------------------------------------------------- #
# rem / viewport unit conversion, empty at-rules, cleanup
# --------------------------------------------------------------------------- #

# Capture an optional leading sign and scientific notation INSIDE the group so
# negative (-2rem) and exponent (1e3rem) values convert. The lookbehind still
# rejects identifier/hex fragments (foorem, #a1rem) but no longer the '-' sign.
REM_RE = re.compile(r"(?<![\w.#])(-?\d*\.?\d+(?:[eE][+-]?\d+)?)rem\b", re.I)
VPUNIT_RE = re.compile(r"(?<![\w.#])(-?\d*\.?\d+(?:[eE][+-]?\d+)?)(vmin|vmax|vw|vh)\b", re.I)
EMPTY_ATRULE_RE = re.compile(r"@(?:media|supports|document|-[\w-]+)\b[^{}]*\{\s*\}", re.I)
EMPTY_RULESET_RE = re.compile(r"(?<![@\w-])([^{}@;]+?)\{\s*\}")
EMPTY_DECL_RE = re.compile(r"([A-Za-z-]+)\s*:\s*(?=[;}]);?")


def convert_rem(css, opts, changes, source):
    if not opts.rem:
        return css
    root = opts.root_font_size

    def repl(m):
        px = float(m.group(1)) * root
        new = fmt_num(px) + "px"
        changes.append({"source": source, "type": "rem",
                        "from": m.group(0), "to": new,
                        "note": "RMSDK treats rem as em; converted to a fixed px"})
        return new

    out, _ = sub_in_code(REM_RE, repl, css)
    return out


def convert_viewport(css, opts, changes, source):
    if not opts.viewport:
        return css

    def repl(m):
        num = float(m.group(1))
        unit = m.group(2).lower()
        base = {"vw": opts.vw_base, "vh": opts.vh_base,
                "vmin": min(opts.vw_base, opts.vh_base),
                "vmax": max(opts.vw_base, opts.vh_base)}[unit]
        new = fmt_num(num / 100.0 * base) + "px"
        changes.append({"source": source, "type": "viewport",
                        "from": m.group(0), "to": new,
                        "note": "viewport units are unreliable/crashing on RMSDK; "
                                "converted against an assumed %dx%d px viewport"
                                % (opts.vw_base, opts.vh_base)})
        return new

    out, _ = sub_in_code(VPUNIT_RE, repl, css)
    return out


def strip_empty_atrules(css, changes, source):
    """Remove empty @media/@supports blocks (crash old RMSDK) and empty rulesets
    left behind by var()/declaration removal, cascading so that emptying a rule
    can in turn empty its enclosing at-rule."""
    total_at = 0
    while True:
        # At-rules FIRST: '@media x { }' must be removed whole, before the
        # ruleset pass could otherwise mistake its prelude 'x ' for an empty
        # selector and leave a dangling '@media'.
        css, na = sub_in_code(EMPTY_ATRULE_RE, lambda m: "", css)
        css, nr = sub_in_code(EMPTY_RULESET_RE, lambda m: "", css)
        total_at += na
        if nr == 0 and na == 0:
            break
    if total_at:
        changes.append({"source": source, "type": "empty-atrule", "from": "",
                        "to": "", "note": "removed %d empty @media/@supports block(s) "
                                          "(crashes old RMSDK)" % total_at})
    return css


def cleanup_empty_decls(css):
    out, _ = sub_in_code(EMPTY_DECL_RE, lambda m: "", css)
    return out


# --------------------------------------------------------------------------- #
# Report-only detection
# --------------------------------------------------------------------------- #

def detect_report_only(css, source, warnings):
    seen = set()
    for is_code, text, start in split_code_segments(css):
        if not is_code:
            continue
        for key, rx, msg in REPORT_ONLY:
            for m in rx.finditer(text):
                line = css.count("\n", 0, start + m.start()) + 1
                dedup = (key, line)
                if dedup in seen:
                    continue
                seen.add(dedup)
                warnings.append({"source": source, "key": key,
                                 "line": line, "snippet": m.group(0).strip(),
                                 "message": msg})


# --------------------------------------------------------------------------- #
# CSS / XHTML processing
# --------------------------------------------------------------------------- #

def sanitize_css_text(css, varmap, opts, changes, warnings, source, is_stylesheet):
    """Apply the full value-level sanitization to a chunk of CSS."""
    detect_report_only(css, source, warnings)          # report-only (pre-edit)
    css = expand_vars(css, varmap)                      # resolve var()
    if is_stylesheet:
        css, _ = strip_var_declarations(css)            # drop --x: ...
    css = eval_funcs(css, opts, changes, source)        # calc/min/max/clamp/env
    css = convert_rem(css, opts, changes, source)       # rem -> px
    css = convert_viewport(css, opts, changes, source)  # vw/vh -> px
    css = cleanup_empty_decls(css)                      # drop "prop: ;"
    if is_stylesheet:
        css = strip_empty_atrules(css, changes, source)
    return css


STYLE_BLOCK_RE = re.compile(r"(<style\b[^>]*>)(.*?)(</style>)", re.I | re.S)
STYLE_ATTR_RE = re.compile(r"""(\sstyle\s*=\s*)(["'])(.*?)(\2)""", re.I | re.S)
# Regions that look like attributes but are not: <style>/<script> bodies and
# HTML comments. style="..." rewriting must skip these to avoid corrupting
# comments, script strings, attribute-selector strings, or prose.
MARKUP_SKIP_RE = re.compile(
    r"(<style\b[^>]*>.*?</style>|<script\b[^>]*>.*?</script>|<!--.*?-->)", re.I | re.S)


def _apply_outside_skips(text, fn):
    """Apply fn (a str->str transform) only to regions outside <style>/<script>
    bodies and HTML comments."""
    out = []
    last = 0
    for m in MARKUP_SKIP_RE.finditer(text):
        out.append(fn(text[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(fn(text[last:]))
    return "".join(out)


def process_xhtml(text, varmap, opts, changes, warnings, source):
    """Sanitize <style> blocks and style="" attributes inside an XHTML/SVG doc."""
    def block_repl(m):
        inner = sanitize_css_text(m.group(2), varmap, opts, changes, warnings,
                                  source + " <style>", is_stylesheet=True)
        return m.group(1) + inner + m.group(3)

    text = STYLE_BLOCK_RE.sub(block_repl, text)

    def attr_repl(m):
        inner = sanitize_css_text(m.group(3), varmap, opts, changes, warnings,
                                  source + ' style="..."', is_stylesheet=False)
        return m.group(1) + m.group(2) + inner + m.group(4)

    return _apply_outside_skips(text, lambda seg: STYLE_ATTR_RE.sub(attr_repl, seg))


def collect_xhtml_vars(text, varmap):
    for m in STYLE_BLOCK_RE.finditer(text):
        collect_custom_properties(m.group(2), varmap)

    def collect_attrs(seg):
        for m in STYLE_ATTR_RE.finditer(seg):
            collect_custom_properties(m.group(3), varmap)
        return seg

    _apply_outside_skips(text, collect_attrs)


# --------------------------------------------------------------------------- #
# EPUB read / repackage
# --------------------------------------------------------------------------- #

MIMETYPE_BYTES = b"application/epub+zip"
SKIP_ENTRIES = ("__MACOSX/", ".DS_Store", "Thumbs.db")


def is_css_name(name):
    return name.lower().endswith(".css")


def is_xhtml_name(name):
    return name.lower().endswith((".xhtml", ".html", ".htm", ".xht"))


def is_svg_name(name):
    return name.lower().endswith(".svg")


def classify(name, media):
    """Return 'css', 'markup', or None for an entry, using the OPF media-type
    when available and falling back to the file extension."""
    mt = media.get(name)
    if is_css_name(name) or mt == "text/css":
        return "css"
    if (is_xhtml_name(name) or is_svg_name(name)
            or mt in ("application/xhtml+xml", "text/html", "image/svg+xml")):
        return "markup"
    return None


def _is_mimetype(name):
    """True for the canonical 'mimetype' and any misplaced/mis-cased copy."""
    return name == "mimetype" or os.path.basename(name).lower() == "mimetype"


def validate_arcname(name):
    """Reject zip-slip / OCF-invalid entry names (absolute, traversal, or
    backslash paths) rather than faithfully repackaging a hostile archive."""
    norm = name.replace("\\", "/")
    if (norm != name or norm.startswith("/") or norm == ".."
            or norm.startswith("../") or "/../" in norm or norm.endswith("/..")):
        raise SystemExit("kobofix: refusing unsafe ZIP entry name (zip-slip): %r" % name)


def _localname(tag):
    return tag.rsplit("}", 1)[-1]


def parse_manifest(entries):
    """Best-effort {arcname: media-type} from META-INF/container.xml -> OPF
    manifest, so resources are classified by declared type, not just extension
    (catches text/css with odd names and SVG with embedded <style>). Returns {}
    on any problem."""
    table = {}
    container = None
    for name, data in entries:
        if name.replace("\\", "/").lower() == "meta-inf/container.xml":
            container = data
            break
    if container is None:
        return table
    try:
        croot = ET.fromstring(container)
    except ET.ParseError:
        return table
    opf_path = None
    for el in croot.iter():
        if _localname(el.tag) == "rootfile" and el.get("full-path"):
            opf_path = el.get("full-path")
            break
    if not opf_path:
        return table
    opf_bytes = next((d for n, d in entries if n == opf_path), None)
    if opf_bytes is None:
        return table
    try:
        oroot = ET.fromstring(opf_bytes)
    except ET.ParseError:
        return table
    opf_dir = posixpath.dirname(opf_path)
    for el in oroot.iter():
        if _localname(el.tag) == "item":
            href, mt = el.get("href"), el.get("media-type")
            if not href or not mt:
                continue
            href = urllib.parse.unquote(href)
            full = posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href
            table[full] = mt
    return table


def retag_to_utf8(text, is_css):
    """Normalise an in-document encoding declaration to UTF-8 so it matches the
    UTF-8 bytes we always write (otherwise a utf-16-declared file re-saved as
    UTF-8 fails strict XML parsers and EPUBCheck). No-op when already UTF-8."""
    if is_css:
        m = CSS_CHARSET_RE.match(text)
        if m and m.group(1).strip().lower() not in ("utf-8", "us-ascii"):
            return CSS_CHARSET_RE.sub('@charset "utf-8";', text, count=1)
        return text
    m = XML_ENC_RE.search(text)
    if m and m.group(2).strip().lower() not in ("utf-8", "us-ascii"):
        return XML_ENC_RE.sub(lambda mm: mm.group(1) + "utf-8" + mm.group(3), text, count=1)
    return text


def decode_text(data):
    """Decode UTF-8/UTF-16/Latin-1, stripping a BOM. Returns (text, encoding)."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), "utf-8-bom"
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16"), "utf-16"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


def load_entries(src):
    """Load an .epub file OR an extracted directory into an ordered list of
    (arcname, bytes). Returns (entries, input_mimetype_report)."""
    entries = []
    report = {"present": False, "first": False, "stored": False, "no_extra": False,
              "exact_bytes": False}
    if os.path.isdir(src):
        names = []
        for root, _dirs, files in os.walk(src):
            for fn in files:
                ap = os.path.join(root, fn)
                arc = os.path.relpath(ap, src).replace(os.sep, "/")
                if any(arc.startswith(p) or arc.endswith(p) for p in SKIP_ENTRIES):
                    continue
                names.append((arc, ap))
        names.sort(key=lambda t: (t[0] != "mimetype", t[0]))
        for arc, ap in names:
            with open(ap, "rb") as fh:
                entries.append((arc, fh.read()))
        for arc, data in entries:
            if arc == "mimetype":
                report.update(present=True, first=(entries[0][0] == "mimetype"),
                              stored=True, no_extra=True,
                              exact_bytes=(data == MIMETYPE_BYTES))
        if not report["present"] and any(_is_mimetype(n) for n, _ in entries):
            report["present"] = True  # exists but misplaced/mis-cased
        return entries, report

    with zipfile.ZipFile(src, "r") as zf:
        infos = zf.infolist()
        for idx, info in enumerate(infos):
            name = info.filename
            if name.endswith("/"):
                continue
            if any(name.startswith(p) or name.endswith(p) for p in SKIP_ENTRIES):
                continue
            validate_arcname(name)
            data = zf.read(info)  # read by ZipInfo so duplicate names keep their own bytes
            entries.append((name, data))
            if name == "mimetype":
                report.update(
                    present=True,
                    first=(idx == 0),
                    stored=(info.compress_type == zipfile.ZIP_STORED),
                    no_extra=(len(info.extra) == 0),
                    exact_bytes=(data == MIMETYPE_BYTES),
                )
        if not report["present"] and any(_is_mimetype(n) for n, _ in entries):
            report["present"] = True  # exists but misplaced/mis-cased
    return entries, report


def write_epub(entries, out_path):
    """Write a spec-correct OCF ZIP: mimetype first/STORED/no-extra, rest DEFLATE.
    Any stray/duplicate mimetype copy in `entries` is dropped."""
    reg = (stat.S_IFREG | 0o644) << 16
    with zipfile.ZipFile(out_path, "w") as zf:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        info.create_system = 3
        info.external_attr = reg
        zf.writestr(info, MIMETYPE_BYTES)
        for name, data in entries:
            if _is_mimetype(name):
                continue
            zi = zipfile.ZipInfo(name)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 3
            zi.external_attr = reg
            zf.writestr(zi, data)


def verify_epub(out_path):
    """Re-open the produced EPUB and assert OCF mimetype rules. Returns list of
    problems (empty == OK)."""
    problems = []
    with zipfile.ZipFile(out_path, "r") as zf:
        infos = zf.infolist()
        if not infos or infos[0].filename != "mimetype":
            problems.append("mimetype is not the first zip entry")
            return problems
        mt = infos[0]
        if mt.compress_type != zipfile.ZIP_STORED:
            problems.append("mimetype is compressed (must be STORED)")
        if len(mt.extra) != 0:
            problems.append("mimetype has a ZIP extra field (must be empty)")
        if zf.read("mimetype") != MIMETYPE_BYTES:
            problems.append("mimetype bytes are not exactly 'application/epub+zip'")
    return problems


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #

def process_epub(src, opts):
    entries, in_mt = load_entries(src)
    media = parse_manifest(entries)

    def kind_of(name):
        return classify(name, media)

    varmap = {}
    # Pass 1: collect all custom-property definitions globally.
    for name, data in entries:
        k = kind_of(name)
        if k == "css":
            text, _ = decode_text(data)
            collect_custom_properties(text, varmap)
        elif k == "markup":
            text, _ = decode_text(data)
            collect_xhtml_vars(text, varmap)

    changes = []
    warnings = []
    new_entries = []
    edited_files = []

    # Pass 2: transform. The canonical mimetype is re-emitted by write_epub, so
    # any mimetype copy here is dropped.
    for name, data in entries:
        if _is_mimetype(name):
            continue
        k = kind_of(name)
        if k == "css":
            text, _enc = decode_text(data)
            new_text = sanitize_css_text(text, varmap, opts, changes, warnings,
                                         name, is_stylesheet=True)
            new_text = retag_to_utf8(new_text, is_css=True)
            if new_text != text:
                edited_files.append(name)
            new_entries.append((name, new_text.encode("utf-8")))
        elif k == "markup":
            text, _enc = decode_text(data)
            new_text = process_xhtml(text, varmap, opts, changes, warnings, name)
            new_text = retag_to_utf8(new_text, is_css=False)
            if new_text != text:
                edited_files.append(name)
            new_entries.append((name, new_text.encode("utf-8")))
        else:
            new_entries.append((name, data))

    result = {
        "input_mimetype": in_mt,
        "varmap_size": len(varmap),
        "changes": changes,
        "warnings": warnings,
        "edited_files": edited_files,
        "entries": new_entries,
    }
    return result


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def packaging_fixes(in_mt):
    fixes = []
    if not in_mt.get("present"):
        fixes.append("added a missing mimetype entry")
    else:
        if not in_mt.get("first"):
            fixes.append("moved mimetype to be the FIRST zip entry")
        if not in_mt.get("stored"):
            fixes.append("stored mimetype UNCOMPRESSED (was DEFLATE)")
        if not in_mt.get("no_extra"):
            fixes.append("removed the ZIP extra field from mimetype")
        if not in_mt.get("exact_bytes"):
            fixes.append("rewrote mimetype to exact bytes 'application/epub+zip'")
    return fixes


def print_text_report(result, out_path, verify_problems, epubcheck):
    changes = result["changes"]
    warnings = result["warnings"]
    by_type = {}
    for c in changes:
        by_type.setdefault(c["type"], []).append(c)

    print("=" * 72)
    print("kobofix report")
    print("=" * 72)

    pkg = packaging_fixes(result["input_mimetype"])
    print("\n[ PACKAGING ]")
    if pkg:
        for f in pkg:
            print("  FIXED  " + f)
    else:
        print("  OK     mimetype packaging was already compliant")
    print("  OK     re-emitted as mimetype-first / STORED / DEFLATE-rest OCF ZIP")

    print("\n[ CSS AUTO-FIXES ]  (these are what make Kobo open & style the book)")
    order = ["env", "calc", "min", "max", "clamp", "viewport", "rem", "empty-atrule"]
    labels = {
        "calc": "calc() resolved", "min": "min() reduced", "max": "max() reduced",
        "clamp": "clamp() reduced", "env": "env() replaced",
        "viewport": "viewport units -> px", "rem": "rem -> px",
        "empty-atrule": "empty @media/@supports removed",
    }
    var_removed = result["varmap_size"]
    if var_removed:
        print("  %3d  custom propert%s resolved & inlined (var() eliminated)"
              % (var_removed, "y" if var_removed == 1 else "ies"))
    any_fix = bool(var_removed)
    for t in order:
        items = by_type.get(t)
        if not items:
            continue
        any_fix = True
        if t == "empty-atrule":
            print("  %3d  %s" % (len(items), labels[t]))
            continue
        print("  %3d  %s" % (len(items), labels[t]))
        for c in items[:4]:
            print("         %s  ->  %s" % (_short(c["from"]), _short(c["to"])))
        if len(items) > 4:
            print("         ... and %d more" % (len(items) - 4))
    if not any_fix:
        print("  (none needed - no RMSDK-breaking CSS value tokens found)")

    print("\n[ MANUAL REVIEW ]  (detected, NOT auto-changed - no safe equivalent)")
    if warnings:
        wseen = {}
        for w in warnings:
            wseen.setdefault(w["key"], []).append(w)
        for key, ws in wseen.items():
            print("  %s  (%d occurrence%s)" % (key, len(ws), "" if len(ws) == 1 else "s"))
            print("      %s" % ws[0]["message"])
            for w in ws[:3]:
                print("        - %s:%d  %s" % (w["source"], w["line"], _short(w["snippet"])))
            if len(ws) > 3:
                print("        ... and %d more" % (len(ws) - 3))
    else:
        print("  (none - no flexbox/grid/transform/etc. detected)")

    print("\n[ OUTPUT ]")
    print("  file:  %s" % out_path)
    if verify_problems:
        print("  SELF-CHECK FAILED:")
        for p in verify_problems:
            print("    - " + p)
    else:
        print("  SELF-CHECK PASSED: mimetype first / STORED / no extra field / exact bytes")
    if epubcheck is not None:
        print("  EPUBCheck: %s" % ("PASSED" if epubcheck["ok"] else "reported issues"))
        if not epubcheck["ok"]:
            for line in epubcheck["lines"][:20]:
                print("    " + line)
    print("")


def _short(s, n=58):
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 3] + "..."


# --------------------------------------------------------------------------- #
# EPUBCheck (optional)
# --------------------------------------------------------------------------- #

def find_epubcheck(epubcheck_path):
    """Resolve a base command (without the epub argument) to invoke EPUBCheck,
    or None if it can't be found. 'auto' tries PATH, then a bundled jar under
    <script dir>/tools/**/epubcheck.jar."""
    if epubcheck_path and epubcheck_path != "auto":
        if epubcheck_path.lower().endswith(".jar"):
            java = shutil.which("java")
            return [java, "-jar", epubcheck_path] if java else None
        return [epubcheck_path]
    exe = shutil.which("epubcheck")
    if exe:
        return [exe]
    java = shutil.which("java")
    if java:
        here = os.path.dirname(os.path.abspath(__file__))
        for jar in sorted(glob.glob(os.path.join(here, "tools", "**", "epubcheck.jar"),
                                    recursive=True)):
            return [java, "-jar", jar]
    return None


def run_epubcheck(epubcheck_path, epub_path):
    base = find_epubcheck(epubcheck_path)
    if not base or not base[0]:
        return {"ok": False, "lines": ["epubcheck not found on PATH; skipped"]}
    try:
        p = subprocess.run(base + [epub_path], capture_output=True, text=True, timeout=300)
        return {"ok": p.returncode == 0, "lines": (p.stdout + p.stderr).splitlines()}
    except Exception as e:  # pragma: no cover
        return {"ok": False, "lines": ["epubcheck failed: %s" % e]}


def run_epubcheck_messages(epubcheck_path, epub_path):
    """Run EPUBCheck (4.x supports --json) and return
    (version, [records], ran, note). Each record is normalised to the same shape
    as kobofix findings so they can be merged into one report."""
    base = find_epubcheck(epubcheck_path)
    if not base or not base[0]:
        return (None, [], False, "EPUBCheck not found - EPUB-spec conformance was NOT verified")
    tmp_fd, tmp_json = tempfile.mkstemp(suffix=".json")
    os.close(tmp_fd)
    try:
        cmd = base + ["--json", tmp_json, "-q", epub_path]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except Exception as e:  # pragma: no cover
            return (None, [], False, "EPUBCheck failed to run: %s" % e)
        try:
            with open(tmp_json, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            out = (proc.stdout + proc.stderr).strip()
            return (None, [], True, "EPUBCheck ran but --json output was unavailable:\n" + out)
        version = (doc.get("checker") or {}).get("checkerVersion")
        recs = []
        for msg in doc.get("messages", []):
            sev = (msg.get("severity") or "").lower()
            locs = msg.get("locations") or [{}]
            for loc in locs:
                recs.append({
                    "engine": "epubcheck",
                    "rule": msg.get("ID") or "",
                    "severity": sev,
                    "source": (loc.get("path") or epub_path),
                    "line": loc.get("line") or 0,
                    "message": " ".join((msg.get("message") or "").split()),
                })
        return (version, recs, True, None)
    finally:
        if os.path.exists(tmp_json):
            os.remove(tmp_json)


# --------------------------------------------------------------------------- #
# Lint (--check) mode: report RMSDK landmines with KOBO-* rule ids, no rewrite
# --------------------------------------------------------------------------- #

FUNC_LINT_RE = re.compile(r"(?:-(?:webkit|moz|o|ms)-)?(calc|min|max|clamp|var|env)\s*\(", re.I)

# Map the report-only layout features to stable KOBO ids.
LAYOUT_RULE = {
    "flexbox": "KOBO-010", "grid": "KOBO-011", "grid-props": "KOBO-011",
    "position": "KOBO-012", "transform": "KOBO-013", "animation": "KOBO-014",
    "object-fit": "KOBO-015", "aspect-ratio": "KOBO-016", "has": "KOBO-017",
    "is-where": "KOBO-018", "writing-mode": "KOBO-019",
}

# severity rank for sorting / summarising
SEV_RANK = {"fatal": 5, "error": 4, "warning": 3, "usage": 2, "info": 1, "": 0}


def _line_of(full_text, pos):
    return full_text.count("\n", 0, pos) + 1


def lint_fragment(frag, base, full_text, source, findings):
    """Scan one CSS fragment for RMSDK landmines, recording findings with line
    numbers computed against the containing file (`full_text`, offset `base`)."""
    for is_code, text, seg_start in split_code_segments(frag):
        if not is_code:
            continue
        off = base + seg_start
        for m in FUNC_LINT_RE.finditer(text):
            fn = m.group(1).lower()
            findings.append({
                "engine": "kobofix", "rule": "KOBO-001", "severity": "error",
                "source": source, "line": _line_of(full_text, off + m.start()),
                "snippet": m.group(0).strip(),
                "message": "CSS function %s() - legacy RMSDK can't parse it and drops the "
                           "ENTIRE stylesheet; the book may render unstyled or refuse to "
                           "open ('corrupted'). Remove/resolve it." % fn,
                "fixable": True,
            })
        for m in VPUNIT_RE.finditer(text):
            findings.append({
                "engine": "kobofix", "rule": "KOBO-002", "severity": "warning",
                "source": source, "line": _line_of(full_text, off + m.start()),
                "snippet": m.group(0).strip(),
                "message": "Viewport unit '%s' - unreliable on RMSDK (no real viewport); "
                           "in a margin it can crash Kobo to a blank screen." % m.group(0).strip(),
                "fixable": True,
            })
        for m in REM_RE.finditer(text):
            findings.append({
                "engine": "kobofix", "rule": "KOBO-004", "severity": "warning",
                "source": source, "line": _line_of(full_text, off + m.start()),
                "snippet": m.group(0).strip(),
                "message": "rem unit - legacy RMSDK renders rem as em, so sizes compound "
                           "through inheritance and come out wrong.",
                "fixable": True,
            })
        for key, rx, msg in REPORT_ONLY:
            for m in rx.finditer(text):
                findings.append({
                    "engine": "kobofix", "rule": LAYOUT_RULE.get(key, "KOBO-019"),
                    "severity": "info" if key == "writing-mode" else "warning",
                    "source": source, "line": _line_of(full_text, off + m.start()),
                    "snippet": m.group(0).strip(), "message": msg, "fixable": False,
                })
    for m in EMPTY_ATRULE_RE.finditer(frag):
        findings.append({
            "engine": "kobofix", "rule": "KOBO-003", "severity": "warning",
            "source": source, "line": _line_of(full_text, base + m.start()),
            "snippet": " ".join(m.group(0).split())[:40],
            "message": "Empty @media/@supports block - can crash older RMSDK.",
            "fixable": True,
        })


def lint_markup(text, source, findings):
    """Lint the <style> blocks and style="" attributes of an XHTML/SVG doc, with
    line numbers relative to the whole document."""
    skip_spans = [(m.start(), m.end()) for m in MARKUP_SKIP_RE.finditer(text)]

    def in_skip(pos):
        return any(a <= pos < b for a, b in skip_spans)

    for m in STYLE_BLOCK_RE.finditer(text):
        lint_fragment(m.group(2), m.start(2), text, source + " <style>", findings)
    for m in STYLE_ATTR_RE.finditer(text):
        if in_skip(m.start()):
            continue
        lint_fragment(m.group(3), m.start(3), text, source + ' style=""', findings)


def lint_epub(src, opts):
    entries, in_mt = load_entries(src)
    media = parse_manifest(entries)
    findings = []
    for f in packaging_fixes(in_mt):
        findings.append({
            "engine": "kobofix", "rule": "KOBO-000", "severity": "error",
            "source": "(package)", "line": 0, "snippet": "mimetype",
            "message": "Packaging: " + f + " - readers (Kobo especially) report a "
                       "mis-packaged mimetype as 'corrupted'.", "fixable": True,
        })
    for name, data in entries:
        k = classify(name, media)
        if k == "css":
            text, _ = decode_text(data)
            lint_fragment(text, 0, text, name, findings)
        elif k == "markup":
            text, _ = decode_text(data)
            lint_markup(text, name, findings)
    return {"findings": findings, "input_mimetype": in_mt}


def _print_check_text(out, input_path, findings, ec):
    ec_version, ec_recs, ec_ran, ec_note = ec

    def p(s=""):
        out.write(s + "\n")

    p("=" * 72)
    p("kobofix --check : Kobo / Adobe-RMSDK readiness report")
    p("=" * 72)
    p("")
    p("Book: %s" % input_path)

    kobo_err = [f for f in findings if f["severity"] == "error"]
    kobo_warn = [f for f in findings if f["severity"] == "warning"]
    kobo_info = [f for f in findings if f["severity"] == "info"]

    p("")
    p("[ KOBO / RMSDK COMPATIBILITY ]  (EPUBCheck does NOT catch these)")
    if not findings:
        p("  OK  no RMSDK-breaking CSS detected")
    else:
        by_rule = {}
        for f in findings:
            by_rule.setdefault(f["rule"], []).append(f)
        for rule in sorted(by_rule):
            items = by_rule[rule]
            p("  %-9s %-7s %d occurrence%s"
              % (rule, items[0]["severity"].upper(), len(items),
                 "" if len(items) == 1 else "s"))
            p("      %s" % _short(items[0]["message"], 100))
            for f in sorted(items, key=lambda x: (x["source"], x["line"]))[:6]:
                loc = f["source"] if not f["line"] else "%s:%d" % (f["source"], f["line"])
                p("        - %s  %s" % (loc, _short(f.get("snippet", ""), 40)))
            if len(items) > 6:
                p("        ... and %d more" % (len(items) - 6))
        fixable = sum(1 for f in findings if f.get("fixable"))
        if fixable:
            p("")
            p('  -> %d of these are auto-fixable. Run:  kobofix "%s"' % (fixable, input_path))

    p("")
    p("[ EPUB SPEC CONFORMANCE (EPUBCheck) ]")
    if not ec_ran:
        p("  SKIPPED - %s" % (ec_note or "not run"))
        p("  (pass --epubcheck PATH\\to\\epubcheck.jar to include spec validation)")
    elif ec_note:
        p("  " + ec_note)
    else:
        errs = [r for r in ec_recs if r["severity"] in ("error", "fatal")]
        warns = [r for r in ec_recs if r["severity"] == "warning"]
        ver = (" v%s" % ec_version) if ec_version else ""
        if not ec_recs:
            p("  OK  EPUBCheck%s: no errors or warnings" % ver)
        else:
            p("  EPUBCheck%s: %d error(s), %d warning(s)" % (ver, len(errs), len(warns)))
            for r in ec_recs[:12]:
                loc = "%s:%d" % (r["source"], r["line"]) if r["line"] else r["source"]
                p("        - [%s] %-8s %s  %s"
                  % (r["severity"].upper(), r["rule"], loc, _short(r["message"], 60)))
            if len(ec_recs) > 12:
                p("        ... and %d more" % (len(ec_recs) - 12))

    p("")
    p("[ SUMMARY ]")
    p("  Kobo/RMSDK : %d error, %d warning, %d info"
      % (len(kobo_err), len(kobo_warn), len(kobo_info)))
    if ec_ran and not ec_note:
        e = [r for r in ec_recs if r["severity"] in ("error", "fatal")]
        w = [r for r in ec_recs if r["severity"] == "warning"]
        p("  EPUBCheck  : %d error, %d warning" % (len(e), len(w)))
    p("")


def run_check(opts):
    lint = lint_epub(opts.input, opts)
    findings = lint["findings"]
    ec = (None, [], False, None)
    if opts.epubcheck is not None:
        ec = run_epubcheck_messages(opts.epubcheck, opts.input)
    _ec_version, ec_recs, _ec_ran, _ec_note = ec

    if opts.report == "json":
        payload = {
            "version": VERSION,
            "input": opts.input,
            "kobo_findings": findings,
            "epubcheck": {"ran": ec[2], "version": ec[0],
                          "messages": ec_recs, "note": ec[3]},
        }
        text = json.dumps(payload, indent=2)
        sys.stdout.write(text + "\n")
        if opts.report_file:
            with open(opts.report_file, "w", encoding="utf-8") as fh:
                fh.write(text)
    else:
        buf = io.StringIO()
        _print_check_text(buf, opts.input, findings, ec)
        sys.stdout.write(buf.getvalue())
        if opts.report_file:
            with open(opts.report_file, "w", encoding="utf-8") as fh:
                fh.write(buf.getvalue())

    ec_error = any(r["severity"] in ("error", "fatal") for r in ec_recs)
    kobo_error = any(f["severity"] == "error" for f in findings)
    if ec_error or kobo_error:
        return 2
    if opts.strict and findings:
        return 1
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser():
    p = argparse.ArgumentParser(
        prog="kobofix",
        description="Make an EPUB safe for Kobo e-readers (Adobe RMSDK).",
    )
    p.add_argument("input", nargs="?", help="input .epub file or extracted folder")
    p.add_argument("-o", "--output", help="output .epub (default: <input>.kobofixed.epub)")
    p.add_argument("--check", action="store_true",
                   help="lint only: report Kobo/RMSDK issues with KOBO-* ids and line "
                        "numbers and, with --epubcheck, merge EPUBCheck's spec report into "
                        "one readout. Writes no epub. Exit 2 if any book-breaking issue.")
    p.add_argument("--dry-run", action="store_true",
                   help="analyze and report only; do not write an output file")
    p.add_argument("--report", choices=["text", "json"], default="text",
                   help="report format (default: text)")
    p.add_argument("--report-file", help="also write the report to this path")
    p.add_argument("--no-rem", dest="rem", action="store_false",
                   help="do NOT convert rem -> px")
    p.add_argument("--root-font-size", type=float, default=16.0,
                   help="px value of 1rem for rem->px conversion (default: 16)")
    p.add_argument("--no-viewport", dest="viewport", action="store_false",
                   help="do NOT convert standalone vw/vh/vmin/vmax -> px")
    p.add_argument("--vw-base", type=int, default=600,
                   help="assumed viewport width px for vw conversion (default: 600)")
    p.add_argument("--vh-base", type=int, default=800,
                   help="assumed viewport height px for vh conversion (default: 800)")
    p.add_argument("--clamp-pick", choices=["min", "pref", "max"], default="pref",
                   help="which clamp() term to keep when none is an absolute length (default: pref)")
    p.add_argument("--epubcheck", nargs="?", const="auto", default=None,
                   help="run EPUBCheck after building (path to epubcheck/epubcheck.jar, "
                        "or no value to auto-detect on PATH)")
    p.add_argument("--strict", action="store_true",
                   help="exit non-zero if any manual-review warnings were found")
    p.add_argument("--selftest", action="store_true",
                   help="run the built-in torture test and exit")
    p.add_argument("--version", action="version", version="kobofix " + VERSION)
    return p


def main(argv=None):
    opts = build_parser().parse_args(argv)

    if opts.selftest:
        return selftest()

    if not opts.input:
        build_parser().error("an input .epub file or folder is required")
    if not os.path.exists(opts.input):
        build_parser().error("input not found: %s" % opts.input)

    if opts.check:
        return run_check(opts)

    out_path = opts.output
    if not out_path:
        base = opts.input.rstrip("/\\")
        if base.lower().endswith(".epub"):
            out_path = base[:-5] + ".kobofixed.epub"
        else:
            out_path = base + ".kobofixed.epub"

    result = process_epub(opts.input, opts)

    verify_problems = []
    epubcheck = None
    if not opts.dry_run:
        # Write to a temp file first, then move into place (atomic-ish).
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".epub")
        os.close(tmp_fd)
        try:
            write_epub(result["entries"], tmp_path)
            verify_problems = verify_epub(tmp_path)
            if opts.epubcheck is not None:
                epubcheck = run_epubcheck(opts.epubcheck, tmp_path)
            shutil.move(tmp_path, out_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    else:
        out_path = "(dry run - nothing written)"

    if opts.report == "json":
        payload = {
            "version": VERSION,
            "output": out_path,
            "packaging_fixes": packaging_fixes(result["input_mimetype"]),
            "vars_resolved": result["varmap_size"],
            "changes": result["changes"],
            "warnings": result["warnings"],
            "edited_files": result["edited_files"],
            "verify_problems": verify_problems,
            "epubcheck": epubcheck,
        }
        text = json.dumps(payload, indent=2)
        print(text)
        if opts.report_file:
            with open(opts.report_file, "w", encoding="utf-8") as fh:
                fh.write(text)
    else:
        # Capture text report to optionally also write to a file.
        buf = io.StringIO()
        _stdout = sys.stdout
        sys.stdout = buf
        try:
            print_text_report(result, out_path, verify_problems, epubcheck)
        finally:
            sys.stdout = _stdout
        sys.stdout.write(buf.getvalue())
        if opts.report_file:
            with open(opts.report_file, "w", encoding="utf-8") as fh:
                fh.write(buf.getvalue())

    if verify_problems:
        return 2
    if opts.strict and result["warnings"]:
        return 1
    return 0


# --------------------------------------------------------------------------- #
# Built-in torture test
# --------------------------------------------------------------------------- #

def selftest():
    print("kobofix selftest: building a torture EPUB with every known landmine...")

    css = """
:root {
  --accent: #c0392b;
  --gap: calc(2px + 3px);
  --cap: 150px;
}
/* a comment with calc(99px) that must NOT be touched */
.copyright img { max-width: min(150px, 30vw); }
.box {
  color: var(--accent);
  padding: var(--gap);
  width: calc(100% - 20px);
  margin-left: calc(2em + 1em);
  font-size: clamp(0.9rem, 2.5vw, 1.4rem);
  max-width: max(200px, 50%);
  height: 50vh;
  border-image-source: url("calc-not-a-function.png");
}
.vcenter { margin: 50vh 0 0 0; }
.title { font-size: 2rem; }
.safe-area { padding-top: env(safe-area-inset-top, 12px); }
.row { display: flex; justify-content: space-between; gap: 1rem; }
.grid { display: grid; grid-template-columns: 1fr 1fr; }
.rot { transform: rotate(-90deg); }
.neg { margin-left: -2rem; top: -50vh; }
.vendor { width: -webkit-calc(100% - 10px); }
.nospace { width: calc(2em+10px); }
.urlbg { background: url(2rem-icon.png); }
@media screen { }
@supports (display:grid) { }
"""

    svg_doc = """<?xml version="1.0" encoding="utf-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <style>rect { width: calc(50% + 10px); height: 2rem; }</style>
  <rect/>
</svg>
"""

    xhtml = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>t</title>
  <style type="text/css">
    p { font-size: clamp(1rem, 4vw, 2rem); color: var(--accent); }
  </style>
</head>
<body>
  <p style="margin: 10vh 0; width: min(90%, 600px); color: var(--accent)">hi</p>
</body>
</html>
"""

    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="id">urn:uuid:test</dc:identifier>
    <dc:title>Torture</dc:title><dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="c" href="style.css" media-type="text/css"/>
    <item id="p" href="page.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="p"/></spine>
</package>
"""

    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""

    # Build a DELIBERATELY broken epub: mimetype compressed AND not first.
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("OEBPS/style.css", css)
        zf.writestr("OEBPS/page.xhtml", xhtml)
        zf.writestr("OEBPS/cover.svg", svg_doc)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("mimetype", "application/epub+zip")  # compressed + last == bad
    bio.seek(0)

    tmpdir = tempfile.mkdtemp(prefix="kobofix_selftest_")
    src = os.path.join(tmpdir, "broken.epub")
    with open(src, "wb") as fh:
        fh.write(bio.getvalue())

    class O:
        rem = True
        root_font_size = 16.0
        viewport = True
        vw_base = 600
        vh_base = 800
        clamp_pick = "pref"

    result = process_epub(src, O())
    out = os.path.join(tmpdir, "fixed.epub")
    write_epub(result["entries"], out)

    problems = list(verify_epub(out))
    failures = []

    # 1) packaging correct
    if problems:
        failures += ["packaging: " + p for p in problems]

    # 2) no banned value-function tokens remain in any css/xhtml/svg code region
    banned = ("calc(", "min(", "max(", "clamp(", "var(", "env(")
    with zipfile.ZipFile(out, "r") as zf:
        css_out = zf.read("OEBPS/style.css").decode("utf-8")
        xhtml_out = zf.read("OEBPS/page.xhtml").decode("utf-8")
        svg_out = zf.read("OEBPS/cover.svg").decode("utf-8")
    for label, body in (("style.css", css_out), ("page.xhtml", xhtml_out),
                        ("cover.svg", svg_out)):
        code = "".join(t for is_code, t, _ in split_code_segments(body) if is_code)
        for b in banned:
            if b in code.lower():
                failures.append("%s still contains %r" % (label, b))

    # 3) specific expected rewrites
    checks = [
        ("min(150px,30vw) -> 150px", "max-width: 150px" in css_out),
        ("calc(2px+3px) via var --gap -> 5px", "padding: 5px" in css_out),
        ("calc(100% - 20px) -> 100%", "width: 100%" in css_out),
        ("calc(2em + 1em) -> 3em", "margin-left: 3em" in css_out),
        ("rem 2rem -> 32px", "font-size: 32px" in css_out),
        ("env fallback -> 12px", "padding-top: 12px" in css_out),
        ("vh 50vh -> 400px", "400px" in css_out),
        ("url() left intact", 'url("calc-not-a-function.png")' in css_out),
        ("comment calc(99px) untouched", "calc(99px)" in css_out),
        ("--accent inlined in xhtml", "#c0392b" in xhtml_out),
        ("--custom-props removed", "--accent" not in css_out),
        ("empty @media removed", "@media screen" not in css_out),
        ("negative rem -> -32px", "margin-left: -32px" in css_out),
        ("negative vh -> -400px", "top: -400px" in css_out),
        ("vendor -webkit-calc resolved", "-webkit-calc" not in css_out),
        ("no-space calc kept clean term", "2em+10px" not in css_out and "width: 2em" in css_out),
        ("unquoted url() left intact", "url(2rem-icon.png)" in css_out),
        ("svg <style> calc reduced", "width: 50%" in svg_out),
        ("svg <style> rem converted", "height: 32px" in svg_out),
    ]
    for label, ok in checks:
        if not ok:
            failures.append("expected: " + label)

    # 4) report-only flags present
    keys = {w["key"] for w in result["warnings"]}
    for need in ("flexbox", "grid", "transform"):
        if need not in keys:
            failures.append("missing manual-review warning: " + need)

    print()
    print("--- produced style.css ---")
    print(css_out.strip())
    print("--- produced page.xhtml (body) ---")
    print(xhtml_out[xhtml_out.find("<body>"):].strip())
    print()

    shutil.rmtree(tmpdir, ignore_errors=True)

    if failures:
        print("SELFTEST FAILED (%d):" % len(failures))
        for f in failures:
            print("  x " + f)
        return 1
    print("SELFTEST PASSED - all landmines handled and packaging is OCF-compliant.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
