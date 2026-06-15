# kobofix

Make an EPUB that **passes `epubcheck` but won't open (or renders broken) on a Kobo**
work on a Kobo. Pure Python, standard library only — no `pip install`.

This is the problem described in
[*"Your EPUB is fine, Kobo disagrees — blame Adobe"*](https://andreklein.net/your-epub-is-fine-kobo-disagrees-blame-adobe/),
generalized to every known cause, not just the one line in that post.

---

## Three ways to use it

- **Web app (no install):** [`web/`](web/) is a 100%-client-side version for
  GitHub Pages — drag in an `.epub`, see what breaks on Kobo, download the fixed
  file. The book never leaves the browser. See [web/README.md](web/README.md).
- **CLI fixer:** `python kobofix.py book.epub` → repaired EPUB.
- **CLI linter:** `python kobofix.py --check book.epub` → report, optionally
  merged with EPUBCheck.

A worked demo on a *real* book: [`make_modern_alice.py`](make_modern_alice.py)
takes real Project Gutenberg *Alice* content + the modern stylesheet a
contemporary authoring tool emits → EPUBCheck passes it clean, kobofix flags 11
book-breaking errors and repairs them.

## Why your "valid" EPUB fails on Kobo

Kobo renders sideloaded `.epub` files through Adobe's **legacy RMSDK** engine,
whose CSS parser is frozen around 2013. Two independent things bite you:

### 1. RMSDK has no CSS fault tolerance

A normal browser, on hitting one CSS declaration it doesn't understand, drops
*that one declaration* and keeps the rest. RMSDK does the opposite: a single
**value-function token it can't parse — `calc()`, `min()`, `max()`, `clamp()`,
`var()`, `env()` — makes it throw away the *entire stylesheet***, and on some
firmware **refuse to open the book** ("this book is corrupted").

The critical consequence: **a fallback declaration placed *before* the modern
one does not help** — the whole sheet (fallback included) is discarded. The
modern construct has to be *removed*, not shadowed. That's the core thing
`kobofix` does.

```css
/* This whole stylesheet is discarded by RMSDK because of one token: */
.copyright img { max-width: min(150px, 30vw); }

/* kobofix rewrites it to: */
.copyright img { max-width: 150px; }
```

### 2. "Corrupted" is often a packaging bug, not CSS

The most common literal-"corrupted" cause is the EPUB's ZIP packaging: the
`mimetype` entry must be **first**, **uncompressed (STORED)**, with **no extra
field** and the **exact bytes** `application/epub+zip`. Many tools (some
calibre save paths, Windows "Send to → compressed folder", naive zip libraries)
violate this and Kobo is the strictest mainstream reader about it. `kobofix`
**always re-emits a spec-correct OCF ZIP**, so it fixes this even when no CSS
needed changing.

`epubcheck` catches neither problem class reliably: the CSS is valid CSS4 so it
passes, and older `epubcheck` didn't flag a compressed `mimetype`. Passing
`epubcheck` is **not** a signal of Kobo/RMSDK compatibility.

---

## Install

Nothing to install. You need Python 3.7+ (tested on 3.12).

```
python kobofix.py --version
```

## Usage

```
python kobofix.py INPUT.epub [-o OUTPUT.epub] [options]
```

Default output is `INPUT.kobofixed.epub` next to the input. The original is
never modified.

```bash
# Fix a book
python kobofix.py mybook.epub

# Choose the output name
python kobofix.py mybook.epub -o mybook-kobo.epub

# Just analyze — write nothing, print the report
python kobofix.py mybook.epub --dry-run

# LINT mode: report Kobo/RMSDK issues (KOBO-* ids + line numbers) WITHOUT changing
# anything, and merge in EPUBCheck's spec report for one combined readout.
python kobofix.py --check mybook.epub --epubcheck path\to\epubcheck.jar

# Machine-readable report
python kobofix.py mybook.epub --report json --report-file report.json

# Also run EPUBCheck on the result (you supply the jar; Java must be on PATH)
python kobofix.py mybook.epub --epubcheck path\to\epubcheck.jar

# Prove the engine works on your machine
python kobofix.py --selftest
```

You can also pass an **already-extracted EPUB folder** as the input; the tool
will read it and produce a correctly-packaged `.epub`.

### Options

| Option | Default | Meaning |
|---|---|---|
| `-o, --output PATH` | `<input>.kobofixed.epub` | Output file |
| `--check` | off | Lint only: report `KOBO-*` issues + line numbers, merge EPUBCheck; write nothing |
| `--dry-run` | off | Analyze and report only; write nothing |
| `--report {text,json}` | `text` | Report format |
| `--report-file PATH` | — | Also write the report to a file |
| `--no-rem` | rem→px on | Don't convert `rem` to `px` |
| `--root-font-size PX` | `16` | Pixel value of `1rem` for the conversion |
| `--no-viewport` | vw/vh→px on | Don't convert standalone `vw/vh/vmin/vmax` |
| `--vw-base PX` | `600` | Assumed reader viewport **width** for `vw` |
| `--vh-base PX` | `800` | Assumed reader viewport **height** for `vh` |
| `--clamp-pick {min,pref,max}` | `pref` | Which `clamp()` term to keep when none is an absolute length |
| `--epubcheck [PATH]` | off | Run EPUBCheck after building (`epubcheck`/`*.jar`, or no value to auto-detect) |
| `--strict` | off | Exit non-zero if any manual-review warnings were found |

Exit codes: `0` clean · `1` `--strict` with warnings · `2` output failed its
own packaging self-check.

---

## What it fixes automatically

These are the changes that make Kobo **open and style** the book. Every one
leaves **zero** residual modern-value tokens in RMSDK-visible CSS.

| Construct | Action |
|---|---|
| `var(--x)` / `--x:` | Resolved to the literal value and inlined; the `--x` declarations are deleted |
| `calc(...)` | Evaluated when units allow (`calc(2em + 1em)`→`3em`); mixed-unit (`calc(100% - 20px)`) keeps the first term and is flagged |
| `min(a,b)` / `max(a,b)` | Reduced to the absolute-length term (`min(150px,30vw)`→`150px`) |
| `clamp(a,b,c)` | Reduced to one static size (prefers an absolute term; else `--clamp-pick`) |
| `env(x, fallback)` | Replaced with the fallback (or removed) |
| `rem` | Converted to `px` (RMSDK mis-treats `rem` as `em`, compounding sizes) |
| `vw/vh/vmin/vmax` | Converted to `px` against the assumed viewport (verified crash: `margin:50vh`) |
| empty `@media {}` / `@supports {}` | Removed (crashes old RMSDK) |
| `mimetype` packaging | Rewritten first / STORED / no-extra / exact bytes |

All of the above are applied inside standalone `.css` files **and** inside
`<style>` blocks and `style="..."` attributes in XHTML.

## What it flags but does NOT change

These have no safe automatic equivalent — converting them risks silently
reflowing your book, so `kobofix` reports them (with file and line) and leaves
the decision to you:

- **flexbox** and **grid** (and grid sub-properties)
- **`position: absolute/fixed/sticky`** in reflowable content
- **`transform`**, **`transition`/`animation`/`@keyframes`**
- **`object-fit`**, **`aspect-ratio`**
- **`:has()`**, **`:is()`/`:where()`**
- **`writing-mode`** — *kept on purpose*; Kobo supports it for vertical CJK text

The typical fix for flex/grid is to wrap the modern rules in
`@supports (display:flex) { … }` (legacy RMSDK skips the whole block) with a
plain block/float fallback outside it.

---

## How to verify the result

1. `kobofix` self-checks its own output (mimetype first/STORED/no-extra/exact).
2. Open the fixed file in **Adobe Digital Editions** — it uses the same RMSDK
   engine as Kobo, so if it opens and styles correctly there, Kobo will too.
   This is the single highest-value manual test.
3. Optionally run EPUBCheck (`--epubcheck`) for container/structural validity.
4. Sideload to an actual Kobo for final confirmation.

---

## Checking mode (`--check`) — the Kobo readiness linter

`kobofix --check book.epub` makes **no changes**. It reports every RMSDK landmine
with a stable rule id and a `file:line` location (correct even inside `<style>`
blocks and `style=""` attributes), and — if you point it at an EPUBCheck jar —
runs EPUBCheck too and prints **one combined report**: spec conformance from the
validator you already trust, plus the Kobo/RMSDK layer EPUBCheck can't see.

```
python kobofix.py --check book.epub --epubcheck path\to\epubcheck.jar
```

If `--epubcheck` is given with no value, kobofix auto-discovers an `epubcheck`
on `PATH` or a bundled `tools/**/epubcheck.jar`. Exit codes: **2** if any
book-breaking issue (a `KOBO-001` function or `KOBO-000` packaging error, or an
EPUBCheck error/fatal), **1** with `--strict` if only warnings, else **0** —
so it drops straight into CI. Use `--report json` for machine output.

### Rule ids

| Rule | Sev. | What |
|---|---|---|
| `KOBO-000` | error | `mimetype` packaging wrong (the literal "corrupted" cause) |
| `KOBO-001` | error | `calc/min/max/clamp/var/env` — drops the whole stylesheet / fails to open |
| `KOBO-002` | warning | viewport unit `vw/vh/vmin/vmax` (in a margin: blank-screen crash) |
| `KOBO-003` | warning | empty `@media`/`@supports` block (crashes old RMSDK) |
| `KOBO-004` | warning | `rem` (rendered as `em` → wrong sizes) |
| `KOBO-010..019` | warn/info | flexbox, grid, position, transform, animation, object-fit, aspect-ratio, `:has()`, `:is()/:where()`, writing-mode |

## Why not just add this to EPUBCheck?

Short answer: **you can't, and you shouldn't have to.** This was researched
against EPUBCheck's actual source and issue history:

- **Upstreaming is out of scope.** EPUBCheck is the *spec-conformance* checker.
  Valid EPUB3 CSS like `calc()` is **not** a conformance violation, and the
  maintainers have repeatedly closed CSS-lint requests as out of scope
  ([publ-cg#69](https://github.com/w3c/publ-cg/issues/69),
  [#935](https://github.com/w3c/epubcheck/issues/935),
  [#149](https://github.com/w3c/epubcheck/issues/149)). In publ-cg#69 — which
  raises this *exact* "old SDK drops the stylesheet on `calc()`/`var()`" problem —
  the lead maintainer's recommendation was to use **user-side severity overrides
  or a separate tool**. The only CSS warning ever accepted (`position:fixed`)
  landed solely because the EPUB CSS Profile itself flags it.
- **`--customMessages` can't add a rule.** It only re-maps the severity/text of
  *existing* message ids; there is no `calc()`-used id to re-map.
- **A fork is possible but costly.** EPUBCheck has no plugin API, so new checks
  mean editing the Java (`CSSHandler.declaration()`), adding `KOBO-*` message
  ids, and rebuilding the jar — which needs a JDK + Maven, perpetual rebasing on
  upstream, and a rename (BSD-3-Clause forbids shipping it as "EPUBCheck").
  EPUBCheck 5.x also requires Java 11+ to run, so a runnable-on-older-readers
  build means forking the **4.2.6** tag (which, conveniently, is the version
  Kobo itself runs).

So `kobofix --check` *is* the integration: it wraps your real, unmodified
EPUBCheck and adds the Kobo layer beside it — exactly the workflow EPUBCheck's
own maintainer recommended. (A source fork that emits native `KOBO-001/002/003`
messages inside EPUBCheck's stream is fully specced in `epubcheck-fork-notes.md`
if a pipeline ever needs that instead.)

## Known limitations

- **Heuristic value substitution.** Reducing `clamp()`/mixed `calc()`/viewport
  units to one static value is inherently lossy. Defaults are sensible
  (`16px`/rem, `600×800` viewport) and tunable, but check any
  layout-critical sizes. All such reductions are listed in the report.
- **Custom-property scope.** Variables are resolved with a global "last
  definition wins" map. Books that redefine the same `--name` differently in
  different selectors (rare in practice; almost all ebook CSS defines them once
  in `:root`) may resolve to the wrong value — review the report.
- **Layout modules are reported, not converted** (see above).
- **DRM / font obfuscation.** The tool does not decrypt DRM'd books and does not
  touch `encryption.xml`; run it on un-DRM'd EPUBs you have the right to edit.
- **It targets the RMSDK reflowable path** (sideloaded `.epub` on Kobo / ADE),
  which is the one that breaks. It does not generate Kobo's `.kepub.epub`.

---

## Sources

The behavior rules are drawn from (and were re-verified against) these:

- André Klein — *Your EPUB is fine, Kobo disagrees — blame Adobe*
- dvschultz/99problems #53 — *Legacy RMSDK will ignore the entire stylesheet if you use `calc()`*
- Jiminy Panoz — *Five interesting facts about Adobe legacy eBook RMSDK* (`calc()` whole-sheet drop, `rem`-as-`em`, empty `@media` crash)
- Readium CSS docs — `CSS21-epub_compat`, `CSS07-variables`
- J-Novel Club forum — *Blank epub on Kobo?* (`margin:50vh` crash)
- EPUB OCF 3.x / W3C EPUB 3.3 — `mimetype` ZIP packaging rules
