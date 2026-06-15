#!/usr/bin/env python3
"""Build samples/alice-modern.epub: the REAL Project Gutenberg 'Alice in
Wonderland' content, restyled with the modern CSS a contemporary authoring tool
(Vellum / InDesign / Pages export) emits -- the exact constructs that make Kobo
reject the book. Packaging is made spec-correct, so the ONLY problems are the
RMSDK-hostile CSS, which is the point of the demo.
"""
import os
import zipfile

import kobofix

SRC = os.path.join("samples", "alice.epub")
OUT = os.path.join("samples", "alice-modern.epub")

# What a 2020s authoring tool routinely produces -- every line here is valid
# EPUB3 CSS that passes epubcheck but trips legacy Adobe RMSDK / Kobo.
MODERN_CSS = b"""

/* ===== Modern edition theme (added by a contemporary authoring tool) ===== */
:root {
  --ink: #1a1a1a;
  --accent: #6a1b9a;
  --space: clamp(1rem, 2.5vw, 2rem);
}
body {
  color: var(--ink);
  max-width: min(40em, 92vw);
  margin: 0 auto;
  padding: var(--space);
  font-size: 1rem;
  line-height: 1.6;
}
h1 { font-size: clamp(1.8rem, 6vw, 3rem); }
h2 { font-size: clamp(1.3rem, 4vw, 2rem); color: var(--accent); }
.titlepage {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  min-height: 100vh;
}
.cover img { max-width: min(100%, 480px); height: auto; }
blockquote {
  margin-left: calc(1em + 2vw);
  border-left: max(2px, 0.2em) solid var(--accent);
  padding-left: 1rem;
}
@media (max-width: 600px) { body { padding: 1rem; } }
@media print { }
"""


def main():
    if not os.path.exists(SRC):
        raise SystemExit("missing %s -- download a Gutenberg Alice epub first" % SRC)

    entries = []
    with zipfile.ZipFile(SRC) as z:
        for info in z.infolist():
            if info.filename.endswith("/"):
                continue
            entries.append((info.filename, z.read(info)))

    media = kobofix.parse_manifest(entries)
    new_entries = []
    injected = False
    for name, data in entries:
        if not injected and kobofix.classify(name, media) == "css":
            data = data + MODERN_CSS
            injected = True
            print("injected modern CSS into:", name)
        new_entries.append((name, data))

    if not injected:
        raise SystemExit("no CSS file found in the source epub to restyle")

    kobofix.write_epub(new_entries, OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
