from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Callable, Generic, Optional, TypeVar
import xml.etree.ElementTree as ET

def local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag

def child(elem: ET.Element, name: str) -> ET.Element:
    for c in elem:
        if local_name(c.tag) == name:
            return c
    raise KeyError(f"No child named {name}")

def children(elem: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in elem if local_name(c.tag) == name]

def text_of(elem: ET.Element) -> str:
    return (elem.text or '').strip()

def attr_of(elem: ET.Element, name: str) -> str:
    return elem.attrib[name]


@dataclass(slots=True)
class IfcVersion:
    version: str


@dataclass(slots=True)
class DefinitionItem:
    name: str
    definition: str

@dataclass(slots=True)
class PropertyType:
    pass

@dataclass(slots=True)
class PropertySingle(PropertyType):
    data_type: str

@dataclass(slots=True)
class PropertyList(PropertyType):
    data_type: str

@dataclass(slots=True)
class PropertyEnum(PropertyType):
    enum_name: str
    enum_items: list[str]

@dataclass(slots=True)
class PropertyReference(PropertyType):
    ref_type: str

@dataclass(slots=True)
class PropertyBounded(PropertyType):
    data_type: str

@dataclass(slots=True)
class PropertyTable(PropertyType):
    defining: str
    defined: str

@dataclass(slots=True)
class PropertyDef(DefinitionItem):
    property_type: PropertyType

@dataclass(slots=True)
class QtoDef(DefinitionItem):
    qto_type: str

@dataclass(slots=True)
class DefinitionSet():
    root_tag: str
    template_type: str
    schema_location: Optional[str]

    ifc_version: IfcVersion
    name: str
    definition: str
    applicability: str
    applicable_classes: list[str]
    applicable_type_value: str

    items_container_tag: str
    item_tag: str
    items: list

def parse_qto_def(elem: ET.Element) -> QtoDef:
    return QtoDef(
        name=text_of(child(elem, "Name")),
        definition=text_of(child(elem, "Definition")),
        qto_type=text_of(child(elem, "QtoType")),
    )


def parse_property_type(elem: ET.Element) -> PropertyType:
    if len(elem) != 1:
        raise ValueError("Expected PropertyType to contain exactly one child type node")

    variant = elem[0]
    kind = local_name(variant.tag)

    if kind in ("TypePropertySingleValue", "TypePropertyListValue", "TypePropertyBoundedValue"):
        if kind == "TypePropertyListValue":
            variant = child(variant, "ListValue")
        dt = child(variant, "DataType")
        return (PropertySingle if kind == "TypePropertySingleValue" else PropertyBounded if kind == "TypePropertyBoundedValue" else PropertyList)(
            data_type=attr_of(dt, "type"),
        )

    if kind == "TypePropertyEnumeratedValue":
        enum_list = child(variant, "EnumList")
        return PropertyEnum(
            enum_name=attr_of(enum_list, "name"),
            enum_items=[text_of(x) for x in children(enum_list, "EnumItem")] if enum_list is not None else [],
        )
    
    if kind == "TypePropertyReferenceValue":
        return PropertyReference(
            variant.attrib['reftype']
        )
    
    if kind == "TypePropertyTableValue":
        dt1 = attr_of(child(child(variant, "DefiningValue"), "DataType"), "type")
        dt2 = attr_of(child(child(variant, "DefinedValue"), "DataType"), "type")
        return PropertyTable(
            dt1, dt2
        )
        
    raise ValueError(f"Unsupported property kind {kind}")

def parse_property_def(elem: ET.Element) -> PropertyDef:
    pt = child(elem, "PropertyType")
    if pt is None:
        raise ValueError("PropertyDef is missing PropertyType")

    return PropertyDef(
        name=text_of(child(elem, "Name")),
        definition=text_of(child(elem, "Definition")),
        property_type=parse_property_type(pt),
    )


# ----------------------------
# Generic parser for both roots
# ----------------------------

@dataclass(frozen=True, slots=True)
class SetSpec():
    root_tag: str
    items_container_tag: str
    item_tag: str
    item_parser: Callable


SET_SPECS: dict[str, SetSpec] = {
    "QtoSetDef": SetSpec(
        root_tag="QtoSetDef",
        items_container_tag="QtoDefs",
        item_tag="QtoDef",
        item_parser=parse_qto_def,
    ),
    "PropertySetDef": SetSpec(
        root_tag="PropertySetDef",
        items_container_tag="PropertyDefs",
        item_tag="PropertyDef",
        item_parser=parse_property_def,
    ),
}


def parse(xml_text: str) -> DefinitionSet[DefinitionItem]:
    root = ET.fromstring(xml_text)
    root_name = local_name(root.tag)

    spec = SET_SPECS.get(root_name)
    if spec is None:
        raise ValueError(f"Unsupported root tag: {root_name}")

    ifc_version_elem = child(root, "IfcVersion")
    if ifc_version_elem is None:
        raise ValueError("Missing IfcVersion")

    applicable_classes_elem = child(root, "ApplicableClasses")
    items_container = child(root, spec.items_container_tag)
    if items_container is None:
        raise ValueError(f"Missing {spec.items_container_tag}")

    items = [spec.item_parser(e) for e in children(items_container, spec.item_tag)]

    return DefinitionSet(
        root_tag=root_name,
        template_type=root.attrib.get("templatetype", ""),
        schema_location=root.attrib.get("{http://www.w3.org/2001/XMLSchema-instance}noNamespaceSchemaLocation"),
        ifc_version=IfcVersion(version=attr_of(ifc_version_elem, "version")),
        name=text_of(child(root, "Name")),
        definition=text_of(child(root, "Definition")),
        applicability=text_of(child(root, "Applicability")),
        applicable_classes=[text_of(e) for e in children(applicable_classes_elem, "ClassName")] if applicable_classes_elem is not None else [],
        applicable_type_value=text_of(child(root, "ApplicableTypeValue")),
        items_container_tag=spec.items_container_tag,
        item_tag=spec.item_tag,
        items=items,
    )

if __name__ == "__main__":
    for fn in Path(sys.argv[1]).glob("*.xml"):
        print(parse(open(fn, encoding='utf-8').read()))