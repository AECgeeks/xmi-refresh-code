"""MD copy tool - copies authored .md docs from a flat folder.

Usage:
    python copy_markdown.py <schema_uml_path> <docs_root> <entity_name> [<entity_name>...]

Example:
    python copy_markdown.py \\
        /path/to/schemas/IfcTunnelDomain.uml \\
        /path/to/docs \\
        IfcTunnel IfcTunnelTypeEnum

Reads the UML to determine kind (uml:Class, uml:Enumeration,
uml:DataType, uml:Constraint functions). Copies markdown from:
    data/integration-work/ifc4x4_mds/<Name>.md

Writes copied markdown to:
    {docs_root}/schemas/<layer>/<schema>/Entities/<Name>.md
    {docs_root}/schemas/<layer>/<schema>/Types/<Name>.md  (for enums/data types)
    {docs_root}/schemas/<layer>/<schema>/Functions/<Name>.md  (for functions)

The source markdown is copied as-is except for prepending the first heading:
    # <Name>

Property sets, quantity sets, property enumerations, and normalized properties
are intentionally unsupported here; use scaffold_md.py for those.
"""
import argparse
import os
import sys
from pathlib import Path

from xmi import Class, Constraint, DataType, Enumeration, Model


def _owning_schema_name(element):
    owner = getattr(element, "owner", None)
    while owner is not None:
        name = getattr(owner, "name", "")
        xmi_id = getattr(owner, "xmi_id", "")
        if name and xmi_id.startswith("pk_"):
            return name
        owner = getattr(owner, "owner", None)
    return None


def find_target_path(uml_path, docs_root, ent_name, kind, schema_name=None):
    """Best-effort path resolution: <docs_root>/schemas/<layer>/<schema>/<dir>/<name>.md.

    Existing docs are preferred when present. Otherwise <schema> is the UML
    filename stem and <layer> is read from <docs_root>/schemas/*/<schema>/.
    """
    docs_root = Path(docs_root)
    schema = schema_name or Path(uml_path).stem  # e.g. IfcSharedBldgElements
    subdir = {
        "entity": "Entities",
        "enum": "Types",
        "type": "Types",
        "function": "Functions",
    }[kind]

    existing = sorted((docs_root / "schemas").glob(f"*/*/{subdir}/{ent_name}.md"))
    if existing:
        return existing[0]

    layer = None
    for cand in (docs_root / "schemas").iterdir() if (docs_root / "schemas").is_dir() else []:
        if (cand / schema).is_dir():
            layer = cand.name
            break
    if layer is None:
        layer = "shared"  # fallback

    return docs_root / "schemas" / layer / schema / subdir / f"{ent_name}.md"


def _copied_markdown_with_heading(name, source_path):
    body = Path(source_path).read_text(encoding="utf-8-sig")
    content = f"# {name}\n\n{body}"
    if not content.endswith("\n"):
        content += "\n"
    return content


def _kind_for_element(name, element):
    if name.startswith(("Pset_", "Qto_", "PEnum_")):
        return None
    if isinstance(element, DataType):
        return "type"
    if isinstance(element, Enumeration):
        return "enum"
    if isinstance(element, Class):
        return "entity"
    if isinstance(element, Constraint) and element.language == "EXPRESS_FUNCTION":
        return "function"
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("uml_path", help="UML file containing the entity/enum/type/function")
    parser.add_argument("docs_root", help="Root docs directory (parent of schemas/)")
    parser.add_argument("names", nargs="+", help="Entity / enum / type / function names to copy")
    parser.add_argument("--force", action="store_true", help="Overwrite existing .md files")
    parser.add_argument("--dry-run", action="store_true", help="Don't write anything, just print")
    parser.add_argument(
        "--source-md-dir",
        default=os.path.join("data", "integration-work", "ifc4x4_mds"),
        help="Flat source directory containing <Name>.md files",
    )
    args = parser.parse_args()

    model = Model.load(args.uml_path)
    source_md_dir = Path(args.source_md_dir)
    written, skipped = 0, 0

    for nm in args.names:
        element = model.by_name(nm)
        if element is None:
            print(f"[skip] {nm}: not found in {args.uml_path}", file=sys.stderr)
            skipped += 1
            continue

        kind = _kind_for_element(nm, element)
        if kind is None:
            print(f"[skip] {nm}: unsupported XMI object {type(element).__name__}", file=sys.stderr)
            skipped += 1
            continue

        source = source_md_dir / f"{nm}.md"
        if not source.exists():
            print(f"[skip] {nm}: source markdown not found at {source}", file=sys.stderr)
            skipped += 1
            continue

        schema_name = _owning_schema_name(element)
        target = find_target_path(args.uml_path, args.docs_root, nm, kind, schema_name=schema_name)
        content = _copied_markdown_with_heading(nm, source)

        if target.exists() and not args.force:
            print(f"[skip] {target} exists; use --force to overwrite", file=sys.stderr)
            skipped += 1
            continue

        if args.dry_run:
            print(f"--- {target} ---")
            print(content)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            print(f"[wrote] {target}")
            written += 1

    print(f"\n{written} written, {skipped} skipped.", file=sys.stderr)


if __name__ == "__main__":
    main()
