#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re

from openpyxl import load_workbook

import psd

MAIN_SHEET = "1 - For initial upload"
PENUM_SHEET = "1 - PENUMs for initial upload"

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


def normalize(value: str) -> str:
    return value.strip()


def load_upload_rows(rows: list[list[str]]) -> list[UploadRow]:
    upload_rows: list[UploadRow] = []
    for row_number, row in enumerate(rows[1:], start=2):
        padded = row + [""] * max(0, 5 - len(row))
        property_name = normalize(padded[0])
        applicable_type = normalize(padded[1])
        set_name = normalize(padded[2])
        definition = normalize(padded[3])
        data_type = normalize(padded[4])

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
        raise ValueError(f"Row {row.row_number} references {data_type}, but it is absent from the PENUM sheet")

    return psd.PropertyEnum(enum_name=data_type, enum_items=enum_items)


def build_definition_set(
    set_name: str,
    rows: list[UploadRow],
    penums: dict[str, list[str]],
    ifc_version: str,
    schema_location: str,
    include_definitions: bool,
) -> psd.DefinitionSet:
    if not set_name.startswith("Pset_"):
        raise ValueError(f"{set_name} is not a Pset_* name; only PropertySetDef output is currently supported")

    applicable_classes = unique_in_order(
        item
        for row in rows
        for item in split_applicable_types(row.applicable_type)
    )

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
                property_type=property_type_from_cell(row, penums),
            )
            for row in rows
        ],
    )


def convert(
    xlsx_path: Path,
    output_dir: Path,
    main_sheet: str,
    penum_sheet: str,
    ifc_version: str,
    schema_location: str,
    include_definitions: bool,
) -> int:
    workbook = load_workbook(xlsx_path, read_only=True, data_only=True)
    try:
        upload_rows = load_upload_rows(read_sheet_rows(workbook, main_sheet))
        penums = load_penums(read_sheet_rows(workbook, penum_sheet))
    finally:
        workbook.close()

    groups: dict[str, list[UploadRow]] = {}
    for row in upload_rows:
        groups.setdefault(row.set_name, []).append(row)

    output_dir.mkdir(parents=True, exist_ok=True)
    for set_name, rows in groups.items():
        definition_set = build_definition_set(
            set_name=set_name,
            rows=rows,
            penums=penums,
            ifc_version=ifc_version,
            schema_location=schema_location,
            include_definitions=include_definitions,
        )
        psd.write(definition_set, output_dir / set_name)

    return len(groups)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert buildingSMART property workbook rows to PSD XML files.")
    parser.add_argument("output_dir", type=Path, help="Directory where generated XML files will be written")
    parser.add_argument("--xlsx", type=Path, default=Path("data") / "4.4 properties.xlsx", help="Input .xlsx workbook")
    parser.add_argument("--sheet", default=MAIN_SHEET, help="Workbook sheet containing property rows")
    parser.add_argument("--penum-sheet", default=PENUM_SHEET, help="Workbook sheet containing PEnum rows")
    parser.add_argument("--ifc-version", default=psd.DEFAULT_IFC_VERSION, help="IfcVersion value for generated PSD files")
    parser.add_argument("--schema-location", default=psd.DEFAULT_SCHEMA_LOCATION, help="PSD schema location")
    parser.add_argument(
        "--include-definitions",
        action="store_true",
        help="Use column D as PropertyDef definitions; by default column D is ignored",
    )
    args = parser.parse_args()

    count = convert(
        xlsx_path=args.xlsx,
        output_dir=args.output_dir,
        main_sheet=args.sheet,
        penum_sheet=args.penum_sheet,
        ifc_version=args.ifc_version,
        schema_location=args.schema_location,
        include_definitions=args.include_definitions,
    )
    print(f"Wrote {count} PSD XML files to {args.output_dir}")


if __name__ == "__main__":
    main()
