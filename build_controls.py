#!/usr/bin/env python3
"""Build two control EPUBs for the EPUBCheck demonstration:
  sample-validpkg-badcss.epub : correct OCF packaging but the ORIGINAL
        RMSDK-breaking CSS (calc/min/var/...) untouched -> should PASS EPUBCheck,
        proving EPUBCheck is blind to the Kobo/RMSDK CSS problem.
"""
import zipfile
import kobofix

# Read the deliberately-broken sample and re-emit with CORRECT packaging only,
# leaving the CSS exactly as-is.
entries = []
with zipfile.ZipFile("sample-broken.epub") as z:
    for info in z.infolist():
        if info.filename.endswith("/"):
            continue
        entries.append((info.filename, z.read(info)))

kobofix.write_epub(entries, "sample-validpkg-badcss.epub")
print("wrote sample-validpkg-badcss.epub (valid OCF packaging, original bad CSS)")
