# Releasing kobofix

Everything below is **ready to run** — the metadata, builds, and git history are
already prepared (commits are authored as `dmang-dev`; no personal info is in any
artifact or commit). You run the outward-facing publish steps yourself.

> Identity note: each repo has a **local** git identity of
> `dmang-dev <282426319+dmang-dev@users.noreply.github.com>` so commits never use
> the machine's global name. Keep using `git` from inside each repo folder.

## 1. GitHub — main repo (`kobofix`)

`gh` is already authenticated as **dmang-dev**. The `origin` remote is set.

```powershell
cd C:\epub
gh repo create dmang-dev/kobofix --public --description "Make EPUBs work on Kobo e-readers (Adobe RMSDK)." --homepage "https://dmang-dev.github.io/kobofix/"
git push -u origin main
```

Then enable the web app: **GitHub → repo → Settings → Pages → Build and
deployment → Source: GitHub Actions**. The included `.github/workflows/pages.yml`
publishes `web/` to `https://dmang-dev.github.io/kobofix/` on every push.

## 2. PyPI — `pip install kobofix`

Artifacts are built in `dist/` and pass `twine check`. Use a PyPI API token.

```powershell
cd C:\epub
python -m build                      # refresh dist/ if you changed anything
python -m twine check dist/*
# optional dry run on TestPyPI first:
# python -m twine upload --repository testpypi dist/*
python -m twine upload dist/*        # prompts for token (user = __token__)
```

## 3. npm — `npm i -g kobofix`

The publishable package lives in `web/` (name `kobofix`, the Node CLI + library;
the web-app files are excluded by the `files` whitelist).

```powershell
cd C:\epub\web
npm login                            # as your npm account
npm publish --access public          # dry run first: npm publish --dry-run
```

## 4. GitHub — EPUBCheck fork (`epubcheck-kobo`)

Separate repo at `C:\epubcheck-kobo` (origin already points to dmang-dev;
`upstream` → w3c/epubcheck).

```powershell
cd C:\epubcheck-kobo
gh repo create dmang-dev/epubcheck-kobo --public --description "Unofficial Kobo/RMSDK-aware fork of EPUBCheck (adds KOBO-001/002/003 warnings)."
git push -u origin main
```

The `.github/workflows/build-kobo.yml` workflow builds the patched jar on
Temurin JDK 8 and uploads it as a downloadable artifact (Actions → run →
Artifacts → `epubcheck-kobo`). Run `epubcheck-kobo` with:
`java -jar epubcheck.jar your-book.epub`.

## Notes

- **Versions are immutable** on PyPI and npm — bump `version` in `pyproject.toml`
  and `web/package.json` (keep them in sync, and with `VERSION` in `kobofix.py`)
  before re-publishing.
- **First publish names**: `kobofix` was confirmed free on PyPI, npm, and GitHub.
- Re-run `python tools\pii_scan.py` after any rebuild if you want to re-verify the
  artifacts carry no personal info.
