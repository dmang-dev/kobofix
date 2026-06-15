# kobofix web — browser-based EPUB Kobo-fixer

A **100% client-side** version of kobofix. The user drags in an `.epub`, sees
exactly what breaks on Kobo (Adobe RMSDK), and downloads a repaired file — and
**the book never leaves their browser** (no server, no upload). Perfect for a
free GitHub Pages tool for writers and publishers.

It runs the *same engine* as the Python CLI, ported to JavaScript and verified
to produce byte-for-byte-equivalent fixes and EPUBCheck-clean output.

## Files

The deployable site is just five files (no build step, no framework):

```
index.html        UI + styles
app.js            browser glue (file picker, report rendering, download)
kobofix.js        EPUB pipeline + JSZip glue   (ES module)
kobocss.js        the CSS engine, ported from kobofix.py   (ES module)
vendor/jszip.min.js   JSZip 3.10.1 (zip read/write), vendored so there's no CDN dependency
```

Everything else in this folder (`node_modules/`, `test/`, `package.json`) is for
local development/testing only and is **not** deployed.

## Run locally

```
# from the repo root
python -m http.server 8000
# open http://localhost:8000/web/index.html
```

(ES modules require HTTP, not `file://`.)

## Test (Node)

`node_modules` carries JSZip for a headless parity test that runs the browser
engine in Node and checks it against the Python CLI + OCF correctness:

```
cd web
npm install          # once
node test/run.mjs
```

There's also an in-browser self-test at `web/test/selftest.html`.

## Deploy to GitHub Pages

**Automatic (recommended):** the repo includes
`.github/workflows/pages.yml`, which assembles a clean site (just the five files
above) and publishes it. In your repo: **Settings → Pages → Build and deployment
→ Source: GitHub Actions**, then push to `main`. Your tool will be live at
`https://<user>.github.io/<repo>/`.

**Manual:** copy `index.html`, `app.js`, `kobofix.js`, `kobocss.js`, and
`vendor/jszip.min.js` into a `docs/` folder (or a repo root) and enable
**Pages → Deploy from a branch → /docs**.

## How it maps to the CLI

| CLI | Web |
|---|---|
| `kobofix --check book.epub` | the report shown on screen (`KOBO-*` ids, line numbers) |
| `kobofix book.epub` | the **Download fixed EPUB** button |
| EPUBCheck merge | not bundled (EPUBCheck is a 12 MB Java app); the report links the two conceptually |

The web app deliberately does **not** run EPUBCheck (it's a Java tool, not
browser-portable). It covers the Kobo/RMSDK layer EPUBCheck can't see; run the
CLI or the desktop EPUBCheck for spec conformance.

## Browser support

Needs a modern browser (ES modules, `File.arrayBuffer`, regex lookbehind):
Chrome/Edge 80+, Firefox 78+, Safari 16.4+. All processing is local and
synchronous-ish; very large books (tens of MB with images) take a few seconds.
