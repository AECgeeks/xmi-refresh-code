#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
import re
import sys

from openpyxl import load_workbook

import psd

MAIN_SHEET = "1 - For initial upload"
PENUM_SHEET = "1 - PENUMs for initial upload"

# CSV-mode defaults — Yousheng's normalized catalog (see ifc4x4_properties/)
MAIN_CSV = "1_for_initial_upload.csv"
PENUM_CSV = "1_penum_initial_upload.csv"

# Work-breakdown CSV format (see ifc4x4_csv_files/IFC4X4_work_breakdown(*).csv):
# the "Kind" column packs sub-fields with a NBSP-slash-NBSP separator, e.g.
#   "P_SINGLEVALUE\xa0/\xa0IfcPositiveLengthMeasure"
#   "P_ENUMERATEDVALUE\xa0/\xa0IfcLabel\xa0/\xa0PEnum_SegmentJointType"
WB_KIND_SEP_RE = re.compile(r"[\s\xa0]*/[\s\xa0]*")

INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')


@dataclass(frozen=True, slots=True)
class UploadRow:
    row_number: int
    property_name: str
    applicable_type: str
    set_name: str
    definition: str
    data_type: str


def cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


def read_sheet_rows(workbook, sheet_name: str) -> list[list[str]]:
    try:
        worksheet = workbook[sheet_name]
    except KeyError as e:
        sheets = ", ".join(workbook.sheetnames)
        raise KeyError(f"Workbook has no sheet {sheet_name!r}; available sheets: {sheets}") from e

    return [
        [cell_text(value) for value in row]
        for row in worksheet.iter_rows(values_only=True)
    ]


def read_csv_rows(path: Path) -> list[list[str]]:
    # Excel CSV exports are typically cp1252 (Windows-Latin-1) with optional BOM.
    # Try utf-8-sig first (handles BOM and UTF-8 sources), fall back to cp1252.
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with path.open(encoding=encoding, newline="") as f:
                return [list(row) for row in csv.reader(f)]
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8/cp1252", b"", 0, 0, f"Could not decode {path}")


def csv_main_to_excel_layout(rows: list[list[str]]) -> list[list[str]]:
    # 1_for_initial_upload.csv columns (two known vintages):
    #   6-col (old): 0 name, 1 applicable_type, 2 pset, 3 definition, 4 data_type, 5 explanation
    #   7-col (new, 2026-05-19 catalog): adds col 4 definition_ext between definition + data_type
    # Excel "1 - For initial upload" columns (per load_upload_rows):
    #   0 name, 1 applicable_type, 2 pset, 3 definition, 4 definition_ext, 5 data_type
    # Auto-detect by header length: pass-through for 7-col; insert empty col 4 for 6-col.
    if not rows:
        return []
    header = rows[0]
    seven_col = len(header) >= 7 and "Local definition" in (header[4] if len(header) > 4 else "")
    out = [header]
    for r in rows[1:]:
        if seven_col:
            padded = r + [""] * max(0, 6 - len(r))
            out.append([padded[0], padded[1], padded[2], padded[3], padded[4], padded[5]])
        else:
            padded = r + [""] * max(0, 5 - len(r))
            out.append([padded[0], padded[1], padded[2], padded[3], "", padded[4]])
    return out


def csv_penum_to_excel_layout(rows: list[list[str]]) -> list[list[str]]:
    # 1_penum_initial_upload.csv columns (two known vintages):
    #   6-col (old): 0 index, 1 enum_name, 2 original_value, 3 initial_upload_value,
    #                4 explanation, 5 original_desc
    #   4-col (new, 2026-05-19 catalog): 0 index, 1 enum_name, 2 value, 3 description
    # Excel "1 - PENUMs for initial upload" columns (per load_penums):
    #   0 _, 1 enum_name, 2 enum_value
    # Auto-detect by header length: 6-col uses col 3 (post-rename); 4-col uses col 2 (only value).
    if not rows:
        return []
    header = rows[0]
    six_col = len(header) >= 6 and "initial upload" in (header[3] if len(header) > 3 else "").lower()
    value_col = 3 if six_col else 2
    out = [header]
    for r in rows[1:]:
        padded = r + [""] * max(0, value_col + 1 - len(r))
        out.append([padded[0], padded[1], padded[value_col]])
    return out


def normalize(value: str) -> str:
    return value.strip()


def load_upload_rows(rows: list[list[str]]) -> list[UploadRow]:
    upload_rows: list[UploadRow] = []
    for row_number, row in enumerate(rows[1:], start=2):
        padded = row + [""] * max(0, 6 - len(row))
        property_name = normalize(padded[0])
        applicable_type = normalize(padded[1])
        set_name = normalize(padded[2])
        definition = normalize(padded[3])
        definition_extension = normalize(padded[4])
        data_type = normalize(padded[5])

        if not any((property_name, applicable_type, set_name, definition, data_type)):
            continue

        missing = [
            name
            for name, value in (
                ("property name", property_name),
                ("applicable type", applicable_type),
                ("pset name", set_name),
                ("data type", data_type),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Row {row_number} is missing {', '.join(missing)}")

        upload_rows.append(
            UploadRow(
                row_number=row_number,
                property_name=property_name,
                applicable_type=applicable_type,
                set_name=set_name,
                definition=definition,
                data_type=data_type,
            )
        )

    return upload_rows


def load_penums(rows: list[list[str]]) -> dict[str, list[str]]:
    penums: dict[str, list[str]] = {}
    for row in rows[1:]:
        padded = row + [""] * max(0, 3 - len(row))
        enum_name = normalize(padded[1])
        enum_value = normalize(padded[2])
        if not enum_name or not enum_value:
            continue
        penums.setdefault(enum_name, [])
        if enum_value not in penums[enum_name]:
            penums[enum_name].append(enum_value)
    return penums


def split_applicable_types(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def unique_in_order(values) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def property_type_from_cell(row: UploadRow, penums: dict[str, list[str]]) -> psd.PropertyType:
    data_type = row.data_type
    if not data_type.casefold().startswith("penum_"):
        return psd.PropertySingle(data_type=data_type)

    penums_by_lower = {name.casefold(): values for name, values in penums.items()}
    enum_items = penums_by_lower.get(data_type.casefold())
    if enum_items is None:
        # Data inconsistency: property references a PEnum not in the PENUMs sheet.
        # Skip with a stderr warning so unrelated rows (other packages) still emit.
        print(
            f"WARN: row {row.row_number}: property {row.property_name!r} references "
            f"{data_type} but it's absent from the PENUM sheet; SKIPPED",
            file=sys.stderr,
        )
        return None

    return psd.PropertyEnum(enum_name=data_type, enum_items=enum_items)


def build_definition_set(
    set_name: str,
    rows: list[UploadRow],
    penums: dict[str, list[str]],
    ifc_version: str,
    schema_location: str,
    qto_schema_location: str,
    include_definitions: bool,
    applicable_type_override: str | None = None,
) -> psd.DefinitionSet:
    # Property rows carry the applicable type per-row; for sets with no
    # property rows (e.g. work-breakdown placeholder psets), fall back to the
    # override supplied by the caller.
    if rows:
        applicable_classes = unique_in_order(
            item
            for row in rows
            for item in split_applicable_types(row.applicable_type)
        )
    elif applicable_type_override:
        applicable_classes = split_applicable_types(applicable_type_override)
    else:
        applicable_classes = []

    if set_name.startswith("Pset_"):
        return psd.DefinitionSet(
            root_tag="PropertySetDef",
            template_type="PSET_TYPEDRIVENOVERRIDE",
            schema_location=schema_location,
            ifc_version=psd.IfcVersion(version=ifc_version),
            name=set_name,
            definition="",
            applicability="",
            applicable_classes=applicable_classes,
            applicable_type_value=",".join(applicable_classes),
            items_container_tag="PropertyDefs",
            item_tag="PropertyDef",
            items=[
                psd.PropertyDef(
                    name=row.property_name,
                    definition=row.definition if include_definitions else "",
                    property_type=pt,
                )
                for row in rows
                if (pt := property_type_from_cell(row, penums)) is not None
            ],
        )

    if set_name.startswith("Qto_"):
        return psd.DefinitionSet(
            root_tag="QtoSetDef",
            template_type="QTO_TYPEDRIVENOVERRIDE",
            schema_location=qto_schema_location,
            ifc_version=psd.IfcVersion(version=ifc_version),
            name=set_name,
            definition="",
            applicability="",
            applicable_classes=applicable_classes,
            applicable_type_value=",".join(applicable_classes),
            items_container_tag="QtoDefs",
            item_tag="QtoDef",
            items=[
                psd.QtoDef(
                    name=row.property_name,
                    definition=row.definition if include_definitions else "",
                    qto_type=row.data_type,
                )
                for row in rows
            ],
        )

    raise ValueError(f"{set_name} is not a Pset_* or Qto_* name")


def _split_wb_kind(kind: str) -> list[str]:
    """Split a work-breakdown Kind cell on /-with-(NBSP-)whitespace."""
    parts = WB_KIND_SEP_RE.split(kind.strip())
    return [p for p in parts if p]


def work_breakdown_to_upload_rows(rows: list[list[str]]) -> list[UploadRow]:
    """Parse the work-breakdown "_Properties Itmes" CSV into UploadRows.

    Columns (per header row): Item ID, Package Code, Package Title, Kind, Name,
    Change, Applicability, Layer, Schema (UML), Notes, ...

    Two row shapes interleave:
      - Pset/Qto header row: Kind in {PSET_TYPEDRIVENOVERRIDE, QTO_TYPEDRIVENOVERRIDE},
        Name=<Pset/Qto name>, Applicability=<applicable IFC class[/predef]>.
        These define the (set_name -> applicable_type) mapping for property rows.
      - Property row: Kind starts with P_/Q_ (or is "0" for under-specified Qto),
        Name=<property name>, Applicability=<owning Pset/Qto name>.

    PENUM / PENUM_VALUE rows are ignored here (the dedicated Penum CSV is the
    source of truth for enum values).
    """
    set_to_applicable: dict[str, str] = {}
    property_rows: list[tuple[int, list[str]]] = []

    # First pass: collect pset/qto header rows.
    for row_number, row in enumerate(rows[1:], start=2):
        padded = row + [""] * max(0, 7 - len(row))
        kind = normalize(padded[3])
        name = normalize(padded[4])
        applicability = normalize(padded[6])
        if kind in ("PSET_TYPEDRIVENOVERRIDE", "QTO_TYPEDRIVENOVERRIDE") and name:
            set_to_applicable[name] = applicability
        elif kind and kind not in ("PENUM", "PENUM_VALUE") and name and applicability:
            property_rows.append((row_number, padded))

    upload_rows: list[UploadRow] = []
    for row_number, padded in property_rows:
        kind = normalize(padded[3])
        property_name = normalize(padded[4])
        set_name = normalize(padded[6])
        applicable_type = set_to_applicable.get(set_name, "")

        # Derive data_type from Kind sub-fields.
        parts = _split_wb_kind(kind)
        head = parts[0] if parts else ""
        if head == "P_ENUMERATEDVALUE" and len(parts) >= 3:
            # P_ENUMERATEDVALUE / IfcLabel / PEnum_XxxType -> use enum name
            data_type = parts[2]
        elif head in ("P_SINGLEVALUE", "P_LISTVALUE", "P_BOUNDEDVALUE") and len(parts) >= 2:
            data_type = parts[1]
        elif set_name.startswith("Qto_"):
            # Q_LENGTH, Q_VOLUME, Q_AREA, or "0" (under-specified): pass through.
            # Skip under-specified qtos — express_to_xmi.py can't resolve type "0".
            # These re-appear in PR-B once the tunnel team fills the column in
            # the work-breakdown CSV.
            if kind == "0":
                print(
                    f"WARN: row {row_number}: Qto {property_name!r} has Kind='0' "
                    f"(under-specified in work-breakdown CSV); SKIPPED",
                    file=sys.stderr,
                )
                continue
            data_type = kind
        else:
            raise ValueError(
                f"Row {row_number}: unrecognised Kind {kind!r} for property "
                f"{property_name!r} in set {set_name!r}"
            )

        if not applicable_type:
            raise ValueError(
                f"Row {row_number}: property {property_name!r} references set "
                f"{set_name!r}, but no PSET_/QTO_ header row defined it"
            )

        upload_rows.append(
            UploadRow(
                row_number=row_number,
                property_name=property_name,
                applicable_type=applicable_type,
                set_name=set_name,
                definition="",
                data_type=data_type,
            )
        )
    return upload_rows


def work_breakdown_penum_rows(rows: list[list[str]]) -> dict[str, list[str]]:
    """Parse the work-breakdown "Penum proposal" CSV into the penums dict.

    Columns: Penum name, Value, Definition Tunnel, Definition bSI.
    The "Penum name" cell is only set on the first value row of each block;
    subsequent rows leave it blank, so we carry the last-seen name forward.
    """
    penums: dict[str, list[str]] = {}
    current: str = ""
    for row in rows[1:]:
        padded = row + [""] * max(0, 2 - len(row))
        name = normalize(padded[0])
        value = normalize(padded[1])
        if name:
            current = name
        if not current or not value:
            continue
        penums.setdefault(current, [])
        if value not in penums[current]:
            penums[current].append(value)
    return penums


def work_breakdown_penum_from_properties(rows: list[list[str]]) -> dict[str, list[str]]:
    """Extract PENUM_VALUE rows from the _Properties Itmes CSV as fallback enum source."""
    penums: dict[str, list[str]] = {}
    for row in rows[1:]:
        padded = row + [""] * max(0, 7 - len(row))
        if normalize(padded[3]) != "PENUM_VALUE":
            continue
        value = normalize(padded[4])
        enum_name = normalize(padded[6])
        if not enum_name or not value:
            continue
        penums.setdefault(enum_name, [])
        if value not in penums[enum_name]:
            penums[enum_name].append(value)
    return penums


def _merge_penums(*sources: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for src in sources:
        for name, values in src.items():
            merged.setdefault(name, [])
            for v in values:
                if v not in merged[name]:
                    merged[name].append(v)
    return merged


def work_breakdown_declared_sets(rows: list[list[str]]) -> dict[str, str]:
    """Return {set_name: applicable_type} for every PSET_/QTO_ header row.

    Used to emit empty PSDs for sets declared in the work-breakdown CSV that
    have no property/qto rows of their own (placeholders for later population).
    """
    declared: dict[str, str] = {}
    for row in rows[1:]:
        padded = row + [""] * max(0, 7 - len(row))
        kind = normalize(padded[3])
        name = normalize(padded[4])
        applicability = normalize(padded[6])
        if kind in ("PSET_TYPEDRIVENOVERRIDE", "QTO_TYPEDRIVENOVERRIDE") and name:
            declared[name] = applicability
    return declared


def _load_inputs_from_work_breakdown(
    properties_csv: Path,
    penum_csv: Path | None,
) -> tuple[list[UploadRow], dict[str, list[str]], dict[str, str]]:
    props_rows = read_csv_rows(properties_csv)
    upload_rows = work_breakdown_to_upload_rows(props_rows)
    declared_sets = work_breakdown_declared_sets(props_rows)
    penums_inline = work_breakdown_penum_from_properties(props_rows)
    penums_external = work_breakdown_penum_rows(read_csv_rows(penum_csv)) if penum_csv else {}
    # Penum proposal CSV is canonical; inline PENUM_VALUE rows are fallback.
    penums = _merge_penums(penums_external, penums_inline)
    return upload_rows, penums, declared_sets


def _load_inputs_from_xlsx(
    xlsx_path: Path,
    main_sheet: str,
    penum_sheet: str,
) -> tuple[list[UploadRow], dict[str, list[str]]]:
    workbook = load_workbook(xlsx_path, read_only=True, data_only=True)
    try:
        upload_rows = load_upload_rows(read_sheet_rows(workbook, main_sheet))
        penums = load_penums(read_sheet_rows(workbook, penum_sheet))
    finally:
        workbook.close()
    return upload_rows, penums


def _load_inputs_from_csv(
    main_csv: Path,
    penum_csv: Path,
) -> tuple[list[UploadRow], dict[str, list[str]]]:
    upload_rows = load_upload_rows(csv_main_to_excel_layout(read_csv_rows(main_csv)))
    penums = load_penums(csv_penum_to_excel_layout(read_csv_rows(penum_csv)))
    return upload_rows, penums


def convert(
    output_dir: Path,
    ifc_version: str,
    schema_location: str,
    qto_schema_location: str,
    include_definitions: bool,
    *,
    xlsx_path: Path | None = None,
    main_sheet: str = MAIN_SHEET,
    penum_sheet: str = PENUM_SHEET,
    main_csv: Path | None = None,
    penum_csv: Path | None = None,
    wb_properties_csv: Path | None = None,
    wb_penum_csv: Path | None = None,
) -> int:
    declared_sets: dict[str, str] = {}
    if xlsx_path is not None:
        upload_rows, penums = _load_inputs_from_xlsx(xlsx_path, main_sheet, penum_sheet)
    elif wb_properties_csv is not None:
        upload_rows, penums, declared_sets = _load_inputs_from_work_breakdown(
            wb_properties_csv, wb_penum_csv
        )
    elif main_csv is not None and penum_csv is not None:
        upload_rows, penums = _load_inputs_from_csv(main_csv, penum_csv)
    else:
        raise ValueError(
            "convert() requires xlsx_path, wb_properties_csv, or both main_csv and penum_csv"
        )

    groups: dict[str, list[UploadRow]] = {}
    for row in upload_rows:
        groups.setdefault(row.set_name, []).append(row)

    # In work-breakdown mode, also emit empty placeholders for declared sets
    # that the CSV does not yet populate with property rows.
    for set_name in declared_sets:
        groups.setdefault(set_name, [])

    output_dir.mkdir(parents=True, exist_ok=True)
    for set_name, rows in groups.items():
        definition_set = build_definition_set(
            set_name=set_name,
            rows=rows,
            penums=penums,
            ifc_version=ifc_version,
            schema_location=schema_location,
            qto_schema_location=qto_schema_location,
            include_definitions=include_definitions,
            applicable_type_override=declared_sets.get(set_name),
        )
        psd.write(definition_set, output_dir / (set_name + ".xml"))

    return len(groups)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert buildingSMART property workbook rows to PSD XML files.")
    parser.add_argument("output_dir", type=Path, help="Directory where generated XML files will be written")
    parser.add_argument("--xlsx", type=Path, default=None, help="Input .xlsx workbook (mutually exclusive with --csv-dir / --csv-main+--csv-penum)")
    parser.add_argument("--sheet", default=MAIN_SHEET, help="Workbook sheet containing property rows")
    parser.add_argument("--penum-sheet", default=PENUM_SHEET, help="Workbook sheet containing PEnum rows")
    parser.add_argument("--csv-dir", type=Path, default=None, help=f"Directory containing {MAIN_CSV} and {PENUM_CSV} (shortcut for --csv-main/--csv-penum)")
    parser.add_argument("--csv-main", type=Path, default=None, help="CSV file with property rows (initial-upload form)")
    parser.add_argument("--csv-penum", type=Path, default=None, help="CSV file with PEnum rows (initial-upload form)")
    parser.add_argument(
        "--work-breakdown-properties",
        type=Path,
        default=None,
        help="CSV file in work-breakdown '_Properties Itmes' form (TM-package format)",
    )
    parser.add_argument(
        "--work-breakdown-penum",
        type=Path,
        default=None,
        help="CSV file in work-breakdown 'Penum proposal' form (optional; "
             "PENUM_VALUE rows in --work-breakdown-properties are used as fallback)",
    )
    parser.add_argument("--ifc-version", default=psd.DEFAULT_IFC_VERSION, help="IfcVersion value for generated PSD files")
    parser.add_argument("--schema-location", default=psd.DEFAULT_SCHEMA_LOCATION, help="PSD schema location")
    parser.add_argument("--qto-schema-location", default=psd.DEFAULT_QTO_SCHEMA_LOCATION, help="QTO schema location")
    parser.add_argument(
        "--include-definitions",
        action="store_true",
        help="Use column D as PropertyDef definitions; by default column D is ignored",
    )
    args = parser.parse_args()

    main_csv = args.csv_main
    penum_csv = args.csv_penum
    if args.csv_dir is not None:
        if main_csv is None:
            main_csv = args.csv_dir / MAIN_CSV
        if penum_csv is None:
            penum_csv = args.csv_dir / PENUM_CSV

    xlsx_given = args.xlsx is not None
    csv_given = main_csv is not None or penum_csv is not None
    wb_given = args.work_breakdown_properties is not None or args.work_breakdown_penum is not None
    if sum(map(bool, (xlsx_given, csv_given, wb_given))) > 1:
        parser.error(
            "Pass exactly one of: --xlsx, --csv-dir/--csv-main/--csv-penum, "
            "or --work-breakdown-properties/--work-breakdown-penum"
        )
    if csv_given and (main_csv is None or penum_csv is None):
        parser.error("CSV mode requires both --csv-main and --csv-penum (or --csv-dir containing both)")
    if wb_given and args.work_breakdown_properties is None:
        parser.error("Work-breakdown mode requires --work-breakdown-properties")
    if not xlsx_given and not csv_given and not wb_given:
        # Backwards-compatible default
        args.xlsx = Path("data") / "4.4 properties.xlsx"

    count = convert(
        output_dir=args.output_dir,
        xlsx_path=args.xlsx,
        main_sheet=args.sheet,
        penum_sheet=args.penum_sheet,
        main_csv=main_csv,
        penum_csv=penum_csv,
        wb_properties_csv=args.work_breakdown_properties,
        wb_penum_csv=args.work_breakdown_penum,
        ifc_version=args.ifc_version,
        schema_location=args.schema_location,
        qto_schema_location=args.qto_schema_location,
        include_definitions=args.include_definitions,
    )
    print(f"Wrote {count} PSD XML files to {args.output_dir}")


if __name__ == "__main__":
    main()
