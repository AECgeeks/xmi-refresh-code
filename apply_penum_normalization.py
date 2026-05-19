"""Apply PEnum normalization from 2_penum_update_applied.csv to a UML file.

Reads the canonical normalized literal lists from
2_penum_update_applied.csv and rewrites each named PEnum block in the UML
to have exactly those literals (in order), preserving the
<packagedElement> shell and using xmi:id pattern en_<PEnum>_<LITERAL>.

This is the PR-B editorial step: PR-A leaves PEnum literals in CSV-strict
form (e.g. .../UNSET/NOTKNOWN/USERDEFINED); PR-B normalizes to the
.../OTHER/NOTKNOWN/UNSET convention (Thomas's rule) and any per-package
literal renames (e.g. ANCHOREDWITHEXPANSIONCASING -> ANCHORED_WITH_EXPANSION_CASING
for PEnum_BoltType).

Usage:
    python3 apply_penum_normalization.py \\
        --uml <path-to-uml> \\
        --penum-csv <path-to-2_penum_update_applied.csv> \\
        --penum-names PEnum_SegmentJointType PEnum_BoltType
"""
import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
import xml.etree.ElementTree as ET


UML_NS = "http://www.eclipse.org/uml2/5.0.0/UML"
XMI_NS = "http://www.omg.org/spec/XMI/20131001"
ET.register_namespace("xmi", XMI_NS)
ET.register_namespace("uml", UML_NS)


def load_canonical_literals(csv_path: Path) -> dict[str, list[str]]:
    """Return {penum_name: [literal_name, ...]} from the 'Values for initial upload' column."""
    out: dict[str, list[str]] = defaultdict(list)
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("Penum Name") or "").strip()
            value = (row.get("Values for initial upload") or "").strip()
            if name and value:
                out[name].append(value)
    return dict(out)


def rewrite_penum_in_xml(uml_path: Path, penum_name: str, literals: list[str]) -> bool:
    """Find the <packagedElement xmi:type='uml:Enumeration' name='X'> and replace its
    ownedLiteral children. Returns True if the PEnum was found and rewritten.

    Uses string-level rewrite to preserve formatting / element order outside the
    target PEnum block.
    """
    text = uml_path.read_text(encoding="utf-8")

    open_tag_marker = (
        f'<packagedElement xmi:type="uml:Enumeration" '
        f'xmi:id="en_{penum_name}" name="{penum_name}"'
    )
    start = text.find(open_tag_marker)
    if start == -1:
        return False

    # Find the matching closing </packagedElement> for this block.
    # PEnums are simple — no nested <packagedElement> inside.
    close_marker = "</packagedElement>"
    close = text.find(close_marker, start)
    if close == -1:
        return False
    close_end = close + len(close_marker)

    # Locate the opening tag's '>' to know where the body starts.
    body_start = text.find(">", start) + 1

    # Build the new body: indent matches existing convention (6 spaces).
    indent = "      "
    new_literals = "\n".join(
        f'{indent}<ownedLiteral xmi:type="uml:EnumerationLiteral" '
        f'xmi:id="en_{penum_name}_{lit}" name="{lit}"/>'
        for lit in literals
    )
    new_body = "\n" + new_literals + "\n    "

    new_text = text[:body_start] + new_body + text[close:close_end] + text[close_end:]
    uml_path.write_text(new_text, encoding="utf-8")
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--uml", type=Path, required=True, help="UML file to modify (in-place)")
    p.add_argument("--penum-csv", type=Path, required=True,
                   help="Path to 2_penum_update_applied.csv")
    p.add_argument("--penum-names", nargs="+", required=True,
                   help="PEnum names to normalize (e.g. PEnum_SegmentJointType PEnum_BoltType)")
    args = p.parse_args()

    canonical = load_canonical_literals(args.penum_csv)

    missing_in_csv = []
    missing_in_uml = []
    rewritten = []
    for name in args.penum_names:
        lits = canonical.get(name)
        if not lits:
            missing_in_csv.append(name)
            continue
        if rewrite_penum_in_xml(args.uml, name, lits):
            rewritten.append((name, lits))
        else:
            missing_in_uml.append(name)

    for name, lits in rewritten:
        print(f"[ok] {name}: {len(lits)} literals -> {','.join(lits)}", file=sys.stderr)
    for name in missing_in_csv:
        print(f"[warn] {name}: not found in {args.penum_csv}", file=sys.stderr)
    for name in missing_in_uml:
        print(f"[warn] {name}: not found in {args.uml}", file=sys.stderr)

    if missing_in_csv or missing_in_uml:
        sys.exit(1)


if __name__ == "__main__":
    main()
