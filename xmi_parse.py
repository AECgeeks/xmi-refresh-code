from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class NamedNode:
    xmi_id: str
    name: str
    kind: str
    package_path: tuple[str, ...]


@dataclass(frozen=True)
class PropertyEnd:
    xmi_id: str
    type_id: str | None
    association_id: str | None


@dataclass(frozen=True)
class AssociationClass:
    xmi_id: str
    name: str
    member_end_ids: tuple[str, ...]
    owned_end_ids: tuple[str, ...]


@dataclass(frozen=True)
class Extraction:
    pset_or_qto_associations: dict[str, list[str]]
    package_hierarchy: list[NamedNode]
    deprecated_entities: set[str]


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def attr_by_suffix(elem: ET.Element, suffix: str, default: str | None = None) -> str | None:
    return next((v for k, v in elem.attrib.items() if k.endswith(suffix)), default)


def xmi_type(elem: ET.Element) -> str:
    return attr_by_suffix(elem, "type", "") or ""


def xmi_id(elem: ET.Element) -> str:
    return attr_by_suffix(elem, "id", "") or ""


def xmi_idref(elem: ET.Element) -> str:
    return attr_by_suffix(elem, "idref", "") or ""


def child_type_id(elem: ET.Element) -> str | None:
    return next((xmi_idref(ch) for ch in elem if local_name(ch.tag) == "type" and xmi_idref(ch)), None)


def is_packaged_element(elem: ET.Element) -> bool:
    return local_name(elem.tag) == "packagedElement"


def is_named_type(kind: str) -> bool:
    return kind in {"uml:Class", "uml:DataType", "uml:PrimitiveType", "uml:Enumeration"}


def is_package_tracked_type(kind: str) -> bool:
    return kind in {"uml:Class", "uml:DataType", "uml:PrimitiveType", "uml:Enumeration"}


def package_path_of(elem: ET.Element, parent_map: dict[ET.Element, ET.Element]) -> tuple[str, ...]:
    path: list[str] = []
    cur = parent_map.get(elem)
    while cur is not None:
        if is_packaged_element(cur) and xmi_type(cur) == "uml:Package":
            name = cur.get("name")
            if name:
                path.append(name)
        cur = parent_map.get(cur)
    return tuple(reversed(path))


def parse_xmi(path: str | Path) -> Extraction:
    root = ET.parse(path).getroot()
    parent_map = {child: parent for parent in root.iter() for child in parent}

    named_nodes = tuple(
        NamedNode(
            xmi_id=xmi_id(elem),
            name=elem.get("name", ""),
            kind=xmi_type(elem),
            package_path=package_path_of(elem, parent_map),
        )
        for elem in root.iter()
        if is_packaged_element(elem)
        and xmi_id(elem)
        and elem.get("name")
        and is_named_type(xmi_type(elem))
    )

    names_by_id = {n.xmi_id: n for n in named_nodes}

    deprecated = {names_by_id[elem.attrib["base_Class"]].name for elem in root.iter() if local_name(elem.tag) == "deprecated"}

    property_ends = tuple(
        PropertyEnd(
            xmi_id=xmi_id(elem),
            type_id=child_type_id(elem),
            association_id=elem.get("association"),
        )
        for elem in root.iter()
        if local_name(elem.tag) in {"ownedEnd", "ownedAttribute"}
        and xmi_id(elem)
    )

    property_type_by_end_id = {
        p.xmi_id: p.type_id
        for p in property_ends
        if p.type_id
    }

    assoc_owned_attribute_type_ids = {}
    for p in property_ends:
        if p.association_id and p.type_id:
            assoc_owned_attribute_type_ids.setdefault(p.association_id, []).append(p.type_id)

    associations = tuple(
        AssociationClass(
            xmi_id=xmi_id(elem),
            name=elem.get("name", ""),
            member_end_ids=tuple(
                xmi_idref(ch)
                for ch in elem
                if local_name(ch.tag) == "memberEnd" and xmi_idref(ch)
            ),
            owned_end_ids=tuple(
                xmi_id(ch)
                for ch in elem
                if local_name(ch.tag) == "ownedEnd" and xmi_id(ch)
            ),
        )
        for elem in root.iter()
        if is_packaged_element(elem)
        and xmi_type(elem) == "uml:AssociationClass"
        and xmi_id(elem)
    )

    def distinct(xs):
        return tuple(dict.fromkeys(xs))

    def association_type_ids(assoc: AssociationClass) -> tuple[str, ...]:
        via_member = tuple(
            property_type_by_end_id[end_id]
            for end_id in assoc.member_end_ids
            if end_id in property_type_by_end_id
        )
        via_owned = tuple(
            property_type_by_end_id[end_id]
            for end_id in assoc.owned_end_ids
            if end_id in property_type_by_end_id
        )
        via_association = tuple(assoc_owned_attribute_type_ids.get(assoc.xmi_id, ()))
        return distinct(via_member + via_owned + via_association)

    def other_end_names(assoc: AssociationClass) -> tuple[tuple[str, str], ...]:
        resolved = tuple(
            names_by_id[type_id]
            for type_id in association_type_ids(assoc)
            if type_id in names_by_id
        )
        psets = tuple(
            node for node in resolved
            if node.kind == "uml:Class" and node.name.startswith(("Pset_", "Qto_"))
        )
        others = tuple(
            node for node in resolved
            if not (node.kind == "uml:Class" and node.name.startswith(("Pset_", "Qto_")))
        )
        return tuple(
            (pset.name, other.name)
            for pset in psets
            for other in others
        )

    pairs = tuple(
        pair
        for assoc in associations
        for pair in other_end_names(assoc)
    )

    grouped: dict[str, list[str]] = {}
    for left, right in pairs:
        grouped.setdefault(left, []).append(right)

    pset_or_qto_associations = {
        key: sorted(set(values))
        for key, values in grouped.items()
    }

    package_hierarchy = sorted(
        (
            node for node in named_nodes
            if is_named_type(node.kind)
        ),
        key=lambda n: (n.kind, n.name, n.package_path),
    )

    return Extraction(
        pset_or_qto_associations=pset_or_qto_associations,
        package_hierarchy=package_hierarchy,
        deprecated_entities=deprecated
    )


def extraction_to_jsonable(extraction: Extraction) -> dict:
    return {
        "pset_or_qto_associations": extraction.pset_or_qto_associations,
        "package_hierarchy": [
            {
                "name": node.name,
                "kind": node.kind,
                "package_path": list(node.package_path),
            }
            for node in extraction.package_hierarchy
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(f"usage: {Path(sys.argv[0]).name} <file.xmi>", file=sys.stderr)
        return 2
    result = parse_xmi(args[0])
    print(json.dumps(extraction_to_jsonable(result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())