#!/usr/bin/env python3
"""Build sample-broken.epub: a valid-per-epubcheck EPUB that Kobo would reject,
for demonstrating kobofix. Deliberately mis-packaged (mimetype compressed and
not first) and full of RMSDK-breaking CSS."""
import io, os, zipfile

CSS = """@charset "utf-8";
:root { --accent: #1a5f7a; --pad: calc(1em + 4px); --cap: 200px; }

body { margin: 5%; font-size: 1rem; line-height: var(--lh, 1.5); }
h1   { font-size: clamp(1.4rem, 5vw, 2.2rem); color: var(--accent); }
.copyright img { max-width: min(150px, 30vw); }
.note { padding: var(--pad); border-left: max(2px, 0.2em) solid var(--accent); }
.cover { height: 100vh; }
.center { margin: 50vh auto 0; }
figure img { width: calc(100% - 2em); }

/* layout that needs manual attention */
.cards { display: flex; gap: 1rem; justify-content: space-between; }
.layout { display: grid; grid-template-columns: 1fr 2fr; }
.badge { transform: rotate(-4deg); position: absolute; top: 0; }
"""

XHTML = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>Chapter 1</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
  <style>p.lead { font-size: clamp(1rem, 3vw, 1.3rem); }</style>
</head>
<body>
  <h1>Chapter One</h1>
  <p class="lead" style="margin-top: 10vh; max-width: min(40em, 90%)">
    The quick brown fox jumps over the lazy dog.
  </p>
  <p class="copyright"><img src="logo.png" alt="logo"/></p>
</body>
</html>
"""

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:550e8400-e29b-41d4-a716-446655440000</dc:identifier>
    <dc:title>kobofix sample</dc:title>
    <dc:language>en</dc:language>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="css"  href="style.css"  media-type="text/css"/>
    <item id="ch1"  href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="logo" href="logo.png" media-type="image/png"/>
    <item id="nav"  href="nav.xhtml"  media-type="application/xhtml+xml" properties="nav"/>
  </manifest>
  <spine><itemref idref="ch1"/></spine>
</package>
"""

# Minimal 1x1 transparent PNG so the chapter's <img> reference resolves.
LOGO_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6200010000050001"
    "0d0a2db40000000049454e44ae426082")

NAV = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>nav</title></head>
<body><nav epub:type="toc"><ol><li><a href="chapter1.xhtml">Chapter One</a></li></ol></nav></body>
</html>
"""

CONTAINER = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample-broken.epub")
# Intentionally WRONG packaging: everything DEFLATE-compressed, mimetype written LAST.
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("META-INF/container.xml", CONTAINER)
    zf.writestr("OEBPS/content.opf", OPF)
    zf.writestr("OEBPS/style.css", CSS)
    zf.writestr("OEBPS/chapter1.xhtml", XHTML)
    zf.writestr("OEBPS/logo.png", LOGO_PNG)
    zf.writestr("OEBPS/nav.xhtml", NAV)
    zf.writestr("mimetype", "application/epub+zip")
print("wrote", out)
