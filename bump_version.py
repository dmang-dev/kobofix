#!/usr/bin/env python3
"""Keep kobofix's version in sync across all three places it lives.

Usage:
    python bump_version.py            # show current versions; exit 1 if out of sync
    python bump_version.py --check    # same as above (handy in CI / pre-release)
    python bump_version.py 1.0.1      # set all three to 1.0.1

Files kept in sync:
    pyproject.toml       version = "..."        (PyPI)
    web/package.json     "version": "..."       (npm)
    kobofix.py           VERSION = "..."         (CLI --version)
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# (label, path, regex with 3 groups: prefix, version, suffix)
TARGETS = [
    ("pyproject.toml", os.path.join(ROOT, "pyproject.toml"),
     re.compile(r'(?m)^(version\s*=\s*")([^"]+)(")')),
    ("web/package.json", os.path.join(ROOT, "web", "package.json"),
     re.compile(r'("version"\s*:\s*")([^"]+)(")')),
    ("kobofix.py", os.path.join(ROOT, "kobofix.py"),
     re.compile(r'(?m)^(VERSION\s*=\s*")([^"]+)(")')),
]

SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)*$")


def read_current():
    """Return list of (label, path, regex, current_version_or_None)."""
    out = []
    for label, path, rx in TARGETS:
        with open(path, "r", encoding="utf-8") as fh:
            m = rx.search(fh.read())
        out.append((label, path, rx, m.group(2) if m else None))
    return out


def show_and_check():
    rows = read_current()
    width = max(len(label) for label, *_ in rows)
    versions = set()
    for label, _path, _rx, ver in rows:
        print("  %-*s  %s" % (width, label, ver if ver else "<NOT FOUND>"))
        versions.add(ver)
    if None in versions:
        print("ERROR: a version string could not be found.")
        return 1
    if len(versions) == 1:
        print("in sync: %s" % versions.pop())
        return 0
    print("OUT OF SYNC: " + ", ".join(sorted(v for v in versions if v)))
    return 1


def bump(new_version):
    if not SEMVER.match(new_version):
        print("ERROR: %r is not a valid semantic version (e.g. 1.0.1)." % new_version)
        return 2
    for label, path, rx, current in read_current():
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        new_text, n = rx.subn(lambda m: m.group(1) + new_version + m.group(3), text, count=1)
        if n != 1:
            print("ERROR: could not update version in %s" % label)
            return 1
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new_text)
        print("  %s: %s -> %s" % (label, current, new_version))
    # verify
    if show_and_check() != 0:
        return 1
    print()
    print("Next:")
    print("  - update CHANGELOG.md (move items from [Unreleased] to a new"
          " [%s] section)" % new_version)
    print("  - git commit -am \"Release v%s\"" % new_version)
    print("  - git tag v%s && git push origin main --tags" % new_version)
    print("    -> release.yml publishes to PyPI + npm via OIDC")
    return 0


def main(argv):
    if not argv or argv[0] in ("--check", "-c"):
        print("Current versions:")
        return show_and_check()
    if argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    return bump(argv[0])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
