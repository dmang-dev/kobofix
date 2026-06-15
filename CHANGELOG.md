# Changelog

All notable changes to **kobofix** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-06-15

Initial release.

### Added
- **CLI fixer** (`kobofix book.epub`): removes/resolves the modern CSS that
  legacy Adobe RMSDK (Kobo / Adobe Digital Editions) can't parse — `calc()`,
  `min()`, `max()`, `clamp()`, `var()` (resolved & inlined), `env()` — converts
  `rem`→`px` and viewport units→`px`, strips empty `@media`/`@supports` blocks,
  and re-packages a spec-correct OCF ZIP (mimetype first / STORED / no extra
  field). Covers standalone `.css`, inline `<style>`, `style="…"`, and SVG.
- **Linter** (`kobofix --check`): reports issues with `KOBO-*` rule ids and line
  numbers, and merges in EPUBCheck's spec report when `--epubcheck` is given.
- **Browser app** (`web/`): a 100% client-side version (drag-drop an EPUB, see
  the report, download the fixed file) — the book never leaves the browser.
  Deployed to GitHub Pages.
- **npm package**: a Node CLI + isomorphic library (`kobocss.js` / `kobofix.js`),
  verified to match the Python tool's output and produce EPUBCheck-clean EPUBs.
- Dual-registry publishing via GitHub Actions OIDC Trusted Publishing (PyPI +
  npm), and a `bump_version.py` helper to keep all version strings in sync.

### Rule ids
`KOBO-000` packaging · `KOBO-001` value functions · `KOBO-002` viewport units ·
`KOBO-003` empty `@media` · `KOBO-004` `rem` · `KOBO-010+` flexbox/grid/transform
(reported for manual review, never auto-rewritten).

[Unreleased]: https://github.com/dmang-dev/kobofix/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/dmang-dev/kobofix/releases/tag/v1.0.0
