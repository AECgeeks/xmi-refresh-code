"""Apply the literal replacements from the supplied IfcActorResource diff.

Usage:
    python normalize_encoding.py "schemas/*.uml"
    python normalize_encoding.py "**/*.uml"
"""
import argparse
import glob
import re
from pathlib import Path


REPLACEMENTS = (
    (
        r'<uml:Model xmlns:uml="http://www\.eclipse\.org/uml2/5\.0\.0/UML" '
        r'xmlns:xmi="http://www\.omg\.org/spec/XMI/20131001" '
        r'xmi:id="(\w+)" name="(\w+)" '
        r'xmi:version="20131001">',
        r'<uml:Model xmi:version="20131001" '
        r'xmlns:xmi="http://www.omg.org/spec/XMI/20131001" '
        r'xmlns:uml="http://www.eclipse.org/uml2/5.0.0/UML" '
        r'xmi:id="\1" name="\2">',
    ),
    (
        "&gt;",
        ">",
    ),
)


def apply_replacements(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    updated = text
    count = 0

    for pattern, replacement in REPLACEMENTS:
        updated, hits = re.subn(pattern, replacement, updated)
        count += hits

    if updated != text:
        path.write_text(updated, encoding="utf-8")

    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("glob", help="Glob of files to rewrite, quoted so the shell does not expand it")
    args = parser.parse_args()

    files = sorted(Path(p) for p in glob.glob(args.glob, recursive=True) if Path(p).is_file())
    changed = 0

    for path in files:
        count = apply_replacements(path)
        if count:
            changed += 1
            print(f"{path}: {count} replacement(s)")

    print(f"changed {changed} of {len(files)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
