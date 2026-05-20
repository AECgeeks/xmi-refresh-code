from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Optional, Union
import xml.etree.ElementTree as ET
from xml.dom import minidom

class ns:
    def __init__(self, ns):
        self.ns = ns
    def __getattr__(self, name: str) -> str:
        return f"{{{self.ns}}}{name}"

XMI = ns("http://www.omg.org/spec/XMI/20131001")
UML = ns("http://www.eclipse.org/uml2/5.0.0/UML")

ET.register_namespace("xmi", XMI.ns)
ET.register_namespace("uml", UML.ns)

def upper_value(v: int | None) -> str:
    return "*" if v is None else str(v)

def parse_upper_value(v: str | None) -> int | None:
    return None if v in (None, "*") else int(v)

def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]

def xmi_type(el: ET.Element) -> str:
    return el.get(XMI.type, "")

def xmi_id(el: ET.Element) -> str:
    return el.get(XMI.id, "")

def child(el: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in el if local_name(c.tag) == name), None)

def children(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if local_name(c.tag) == name]

def child_text(el: ET.Element, name: str) -> str:
    c = child(el, name)
    return (c.text if c is not None else None) or ""

def bool_attr(el: ET.Element, name: str, default: bool = False) -> bool:
    v = el.get(name)
    return default if v is None else v.lower() == "true"

def generate_id(obj) -> str:
    def slug(name):
        parts = name.split('_')
        while parts[-1].endswith(']'):
            parts = parts[:-1]
        return "_".join(parts).removeprefix("INV_").removesuffix("?").replace('.', '_')
    if isinstance(obj, Model):
        return f"md_{slug(obj.name)}"
    if isinstance(obj, Package):
        return f"pk_{slug(obj.name)}"
    if isinstance(obj, Class):
        return f"cl_{slug(obj.name)}"
    if isinstance(obj, DataType):
        return f"dt_{slug(obj.name)}"
    if isinstance(obj, Enumeration):
        return f"en_{slug(obj.name)}"
    if isinstance(obj, Attribute):
        return f"at_{slug(obj.owner.name)}_{slug(obj.name)}"
    if isinstance(obj, DirectedAssociation):
        if '_' in slug(obj.name):
            return f"as_{slug(obj.name)}"
        else:
            return f"as_{slug(obj.source.name)}_{slug(obj.name)}"
    if isinstance(obj, Dependency):
        return f"dp_{slug(obj.source.name)}_{slug(obj.target.name)}"
    if isinstance(obj, UnionType):
        return f"un_{slug(obj.name)}"
    if isinstance(obj, Constraint):
        if not obj.owner.xmi_id.startswith("pk_"):
            own = slug(obj.owner.name) + "_"
        else:
            own = ""
        return f"ct_{own}{slug(obj.name)}"
    if isinstance(obj, Import):
        return f"ip_{obj.name.split('.')[0]}"
    raise TypeError(f"Cannot generate an XMI id for {type(obj).__name__}")


REFERENCE_ATTRIBUTES = frozenset({
    "association",
    "client",
    "general",
    "importedPackage",
    "supplier",
    "type",
})
MULTI_REFERENCE_ATTRIBUTES = frozenset({
    "memberEnd",
})
REFERENCE_ELEMENTS = REFERENCE_ATTRIBUTES | MULTI_REFERENCE_ATTRIBUTES

@dataclass(kw_only=True)
class Base:
    xmi_id_: Optional[str] = None
    owner: 'Optional[Base]' = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.xmi_id_ is None:
            self.xmi_id_ = generate_id(self)

    @property
    def xmi_id(self) -> str:
        assert self.xmi_id_ is not None
        return self.xmi_id_

    def root_model(self) -> "Model":
        root: Base = self
        while not isinstance(root, Model):
            if root.owner is None:
                raise ValueError(f"{type(self).__name__} is not attached to a model")
            root = root.owner
        return root

@dataclass(eq=False)
class Import(Base):
    name: str
    imported: "Namespace | None" = None

@dataclass(eq=False)
class DataType(Base):
    name: str
    super: list[DataType] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)

    def rule(
        self,
        name: str,
        body: str,
        language: str = "EXPRESS"
    ) -> Constraint:
        a = Constraint(name=name, language=language, body=body, owner=self)
        self.constraints.append(a)
        return a
    
@dataclass(eq=False)
class Enumeration(Base):
    name: str
    literals: list[str] = field(default_factory=list)

@dataclass(eq=False)
class Constraint(Base):
    name: str
    language: str
    body: str

@dataclass(eq=False)
class UnionType(Base):
    name: str
    members: list['TypeRef'] = field(default_factory=list)

TypeRef = Union["Class", DataType, Enumeration, UnionType]

@dataclass(eq=False)
class Attribute(Base):
    name: str
    type: TypeRef
    lower: int = 0
    upper: int | None = 1
    ordered: bool = False
    association: "DirectedAssociation | None" = None
    derived: str | None = None
    comment: str | None = None

@dataclass(eq=False)
class Relationship(Base):
    name: str
    source: "Class"
    target: "Class | UnionType"

@dataclass(eq=False)
class Dependency(Relationship):
    pass

@dataclass(eq=False)
class DirectedAssociation(Relationship):
    source_role: str
    target_role: str
    source_lower: int = 0
    source_upper: int | None = 1
    target_lower: int = 0
    target_upper: int | None = 1
    ordered: bool = False
    owning_attribute: Attribute | None = None
    source_end_id: str | None = None
    target_end_id: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.source_end_id is None:
            self.source_end_id = f"{self.xmi_id}_source_end"
        if self.target_end_id is None:
            self.target_end_id = f"{self.xmi_id}_target_end"


@dataclass(eq=False)
class Class(Base):
    name: str
    attributes: list[Attribute | Constraint] = field(default_factory=list)
    super: list[Class] = field(default_factory=list)
    comment: Optional[str] = None
    is_abstract: bool = False

    def supertype_names(self):
        seen: set[str] = set()

        def walk(cls: Class):
            if cls.name in seen:
                return
            seen.add(cls.name)
            yield cls.name
            for sup in cls.super:
                yield from walk(sup)

        yield from walk(self)

    def attr(
        self,
        name: str,
        type: TypeRef,
        lower: int = 0,
        upper: int | None = 1,
        ordered: bool = False,
        derived: Optional[str] = None,
        comment: Optional[str] = None
    ) -> Attribute:
        a = Attribute(name=name, type=type, lower=lower, upper=upper, ordered=ordered, owner=self, derived=derived, comment=comment)
        self.attributes.append(a)
        return a

    def rule(
        self,
        name: str,
        body: str,
        language: str = "EXPRESS"
    ) -> Constraint:
        a = Constraint(name=name, language=language, body=body, owner=self)
        self.attributes.append(a)
        return a
    
    def directed_association(
        self,
        name: str,
        target: "Class | UnionType",
        source_role: str,
        target_role: str,
        source_lower: int = 0,
        source_upper: int | None = 1,
        target_lower: int = 0,
        target_upper: int | None = 1,
        ordered: bool = False,
        owned_attribute = True,
    ) -> DirectedAssociation:
        assoc = DirectedAssociation(
            name=name,
            source=self,
            target=target,
            source_role=source_role,
            target_role=target_role,
            source_lower=source_lower,
            source_upper=source_upper,
            target_lower=target_lower,
            target_upper=target_upper,
            ordered=ordered,
            owner=self,
        )
        self.owner.elements.append(assoc)
        if owned_attribute:
            attr = Attribute(
                name=target_role,
                type=target,
                lower=target_lower,
                upper=target_upper,
                ordered=ordered,
                association=assoc,
                owner=self
            )
            assoc.owning_attribute = attr
            assoc.source_end_id = attr.xmi_id
            self.attributes.append(attr)           
        return assoc


@dataclass(eq=False)
class Namespace(Base):
    name: str
    elements: list[Base] = field(default_factory=list)

    def walk(self, _seen: set[int] | None = None):
        if _seen is None:
            _seen = set()
        if id(self) in _seen:
            return
        _seen.add(id(self))
        yield self
        for element in self.elements:
            if isinstance(element, Namespace):
                yield from element.walk(_seen)
            else:
                yield element
                if isinstance(element, Import) and element.imported is not None:
                    yield from element.imported.walk(_seen)
            if isinstance(element, Class):
                yield from element.attributes
            elif isinstance(element, DataType):
                yield from element.constraints

    def by_id(self, xmi_id: str) -> Base | None:
        return next((element for element in self.walk() if element.xmi_id == xmi_id), None)

    def by_name(self, name: str) -> Base | None:
        return next((element for element in self.walk() if re.split(r'_\w{1,2}\[', getattr(element, "name", ''))[0] == name), None)

    def datatype(self, name: str, super: 'Optional[DataType]' = None) -> DataType:
        t = DataType(name=name, super=[super] if super else [], owner=self)
        self.elements.append(t)
        return t

    def cls(self, name: str, super: Class | None = None, comment: Optional[str] = None, is_abstract: bool = False) -> Class:
        c = Class(name=name, super=[super] if super else [], comment=comment, owner=self, is_abstract=is_abstract)
        self.elements.append(c)
        return c

    def package(self, name: str) -> "Package":
        p = Package(name=name, owner=self)
        self.elements.append(p)
        return p
    
    def enumeration(self, name: str, elements: list[str]) -> Enumeration:
        e = Enumeration(name=name, literals=elements, owner=self)
        self.elements.append(e)
        return e

    def union(self, name: str, members: list[TypeRef]) -> UnionType:
        u = UnionType(
            name=name,
            members=members,
            owner=self,
        )
        self.elements.append(u)
        return u

    def constraint(self, name: str, language: str, body: str) -> Constraint:
        c = Constraint(name=name, language=language, body=body, owner=self)
        self.elements.append(c)
        return c
    
    def dependency(self, source: TypeRef, target: TypeRef) -> Dependency:
        d = Dependency(f"Dependency{source.name}{target.name}", source, target, owner=self)
        self.elements.append(d)
        return d

@dataclass(eq=False)
class Package(Namespace):
    pass

@dataclass(eq=False)
class Model(Namespace):
    _next_id: int = field(default=1, init=False, repr=False)

    def referenced_models(self):
        seen: set[int] = set()

        def walk_model(model: Model):
            if id(model) in seen:
                return
            seen.add(id(model))
            yield model
            for element in model.elements:
                if isinstance(element, Import) and element.imported is not None:
                    yield from walk_model(element.imported.root_model())

        yield from walk_model(self)

    def _reserve_ids_from_xml(self, root: ET.Element) -> None:
        max_suffix = 0
        for el in root.iter():
            for value in el.attrib.values():
                for match in re.finditer(r"\b[A-Za-z]+_(\d+)\b", value):
                    max_suffix = max(max_suffix, int(match.group(1)))
        self._next_id = max(self._next_id, max_suffix + 1)

    @classmethod
    def load(cls, path: str | Path) -> "Model":
        return cls.from_file(path)

    @classmethod
    def from_file(cls, path: str | Path) -> "Model":
        path = Path(path)
        return cls.from_element(
            ET.parse(path).getroot(),
            base_path=path.parent,
            _loaded_imports={},
            _current_path=path.resolve(),
        )

    @classmethod
    def from_xml(cls, xml: str) -> "Model":
        return cls.from_element(ET.fromstring(xml))

    @classmethod
    def from_element(
        cls,
        root: ET.Element,
        base_path: Path | None = None,
        _loaded_imports: dict[Path, "Model | None"] | None = None,
        _current_path: Path | None = None,
    ) -> "Model":
        model_el = root if local_name(root.tag) == "Model" else next(
            el for el in root if local_name(el.tag) == "Model"
        )
        model = cls(
            name=model_el.get("name", ""),
            xmi_id_=xmi_id(model_el) or None,
        )
        if _loaded_imports is not None and _current_path is not None:
            _loaded_imports[_current_path] = model

        by_id: dict[str, Base] = {model.xmi_id: model}
        class_elems: list[tuple[Class, ET.Element]] = []
        datatype_elems: list[tuple[DataType, ET.Element]] = []
        association_elems: list[tuple[Namespace, ET.Element, int]] = []
        dependency_elems: list[tuple[Namespace, ET.Element, int]] = []

        def load_import(imported_package: str) -> Namespace | None:
            if base_path is None or "#" not in imported_package:
                return None
            file_part, ref_id = imported_package.rsplit("#", 1)
            import_path = (base_path / file_part).resolve()
            if not import_path.exists():
                return None
            if _loaded_imports is None:
                loaded_imports: dict[Path, Model | None] = {}
            else:
                loaded_imports = _loaded_imports
            if import_path in loaded_imports:
                imported_model = loaded_imports[import_path]
                return None if imported_model is None else imported_model.by_id(ref_id)
            loaded_imports[import_path] = None
            imported_model = cls.from_element(
                ET.parse(import_path).getroot(),
                base_path=import_path.parent,
                _loaded_imports=loaded_imports,
                _current_path=import_path,
            )
            loaded_imports[import_path] = imported_model
            return imported_model.by_id(ref_id) or imported_model

        def resolve_href(href: str) -> Base:
            if not href:
                raise ValueError("Cannot resolve an empty href")
            resolved = load_import(href)
            if resolved is None:
                raise ValueError(f"Cannot resolve href {href!r}")
            return resolved

        def register(obj: Base) -> None:
            if obj.xmi_id:
                by_id[obj.xmi_id] = obj

        def parse_spec(el: ET.Element) -> tuple[str, str]:
            spec = child(el, "specification")
            if spec is None:
                return "", ""
            return child_text(spec, "language"), child_text(spec, "body")

        def parse_constraint(el: ET.Element, owner: Base) -> Constraint:
            language, body = parse_spec(el)
            return Constraint(
                name=el.get("name", ""),
                language=language,
                body=body,
                owner=owner,
                xmi_id_=xmi_id(el) or None,
            )

        def parse_comment(el: ET.Element) -> str | None:
            c = child(el, "ownedComment")
            if c is None:
                return None
            return child_text(c, "body")

        def parse_lower_upper(el: ET.Element) -> tuple[int, int | None]:
            lower = child(el, "lowerValue")
            upper = child(el, "upperValue")
            return (
                int(lower.get("value", "0")) if lower is not None else 0,
                parse_upper_value(upper.get("value") if upper is not None else "1"),
            )

        def ref_obj(el: ET.Element, name: str) -> Base:
            ref_id = el.get(name)
            if ref_id:
                return by_id[ref_id]
            ref_el = child(el, name)
            if ref_el is None:
                raise ValueError(f"Missing {name!r} reference on {local_name(el.tag)}")
            if href := ref_el.get("href"):
                ref_id = href.rsplit("#", 1)[-1]
                return by_id.get(ref_id) or resolve_href(href)
            ref_id = ref_el.get(XMI.idref)
            if not ref_id:
                raise ValueError(f"Missing id in {name!r} reference on {local_name(el.tag)}")
            return by_id[ref_id]

        def read_namespace(ns: Namespace, ns_el: ET.Element) -> None:
            for el in ns_el:
                tag = local_name(el.tag)
                if tag != "packagedElement":
                    continue
                kind = xmi_type(el)
                name = el.get("name", "")
                if kind == "uml:Package":
                    pkg = Package(name=name, owner=ns, xmi_id_=xmi_id(el) or None)
                    ns.elements.append(pkg)
                    register(pkg)
                    read_namespace(pkg, el)
                elif kind in {"uml:DataType", "uml:PrimitiveType"}:
                    dt = DataType(name=name, owner=ns, xmi_id_=xmi_id(el) or None)
                    ns.elements.append(dt)
                    register(dt)
                    datatype_elems.append((dt, el))
                elif kind == "uml:Enumeration":
                    enum = Enumeration(
                        name=name,
                        literals=[lit.get("name", "") for lit in children(el, "ownedLiteral")],
                        owner=ns,
                        xmi_id_=xmi_id(el) or None,
                    )
                    ns.elements.append(enum)
                    register(enum)
                elif kind == "uml:Class":
                    if xmi_id(el).startswith("un_"):
                        union = UnionType(name=name, owner=ns, xmi_id_=xmi_id(el) or None)
                        ns.elements.append(union)
                        register(union)
                    else:
                        klass = Class(
                            name=name,
                            owner=ns,
                            comment=parse_comment(el),
                            is_abstract=bool_attr(el, "isAbstract"),
                            xmi_id_=xmi_id(el) or None,
                        )
                        ns.elements.append(klass)
                        register(klass)
                        class_elems.append((klass, el))
                elif kind == "uml:Constraint":
                    constraint = parse_constraint(el, ns)
                    ns.elements.append(constraint)
                    register(constraint)
                elif kind == "uml:Association":
                    association_elems.append((ns, el, len(ns.elements)))
                elif kind == "uml:Dependency":
                    dependency_elems.append((ns, el, len(ns.elements)))

            for el in ns_el:
                if local_name(el.tag) != "packageImport":
                    continue
                if imported_package := el.get("importedPackage", None):
                    pass
                else:
                    imported_package = child(el, "importedPackage").get("href")
                imp = Import(
                    name=imported_package,
                    imported=load_import(imported_package),
                    owner=ns,
                    xmi_id_=xmi_id(el) or None,
                )
                ns.elements.append(imp)
                register(imp)

        read_namespace(model, model_el)

        for dt, el in datatype_elems:
            for gen in children(el, "generalization"):
                dt.super.append(ref_obj(gen, "general"))  # type: ignore[arg-type]
            for rule_el in children(el, "ownedRule"):
                constraint = parse_constraint(rule_el, dt)
                dt.constraints.append(constraint)
                register(constraint)

        attributes_by_association: dict[str, list[Attribute]] = {}

        for klass, el in class_elems:
            for gen in children(el, "generalization"):
                klass.super.append(ref_obj(gen, "general"))  # type: ignore[arg-type]
            for attr_el in children(el, "ownedAttribute"):
                lower, upper = parse_lower_upper(attr_el)
                default_value = child(attr_el, "defaultValue")
                attr = Attribute(
                    name=attr_el.get("name", ""),
                    type=ref_obj(attr_el, "type"),  # type: ignore[arg-type]
                    lower=lower,
                    upper=upper,
                    ordered=bool_attr(attr_el, "isOrdered"),
                    derived=child_text(default_value, "body") if default_value is not None else None,
                    comment=parse_comment(attr_el),
                    owner=klass,
                    xmi_id_=xmi_id(attr_el) or None,
                )
                klass.attributes.append(attr)
                register(attr)
                if association_id := attr_el.get("association"):
                    attributes_by_association.setdefault(association_id, []).append(attr)
            for rule_el in children(el, "ownedRule"):
                constraint = parse_constraint(rule_el, klass)
                klass.attributes.append(constraint)
                register(constraint)

        _to_insert: list[tuple[Namespace, int, Base]] = []

        for ns, el, idx in association_elems:
            assoc_id = xmi_id(el)
            owned_ends = children(el, "ownedEnd")
            owning_attribute = next(iter(attributes_by_association.get(assoc_id, [])), None)
            if owning_attribute is not None:
                end = owned_ends[0]
                source = ref_obj(end, "type")
                if not isinstance(source, Class):
                    raise TypeError(f"Association {assoc_id} source is not a class")
                source_lower, source_upper = parse_lower_upper(end)
                association = DirectedAssociation(
                    name=el.get("name", ""),
                    source=source,
                    target=owning_attribute.type,  # type: ignore[arg-type]
                    source_role=end.get("name", ""),
                    target_role=owning_attribute.name,
                    source_lower=source_lower,
                    source_upper=source_upper,
                    target_lower=owning_attribute.lower,
                    target_upper=owning_attribute.upper,
                    ordered=owning_attribute.ordered,
                    owning_attribute=owning_attribute,
                    source_end_id=owning_attribute.xmi_id,
                    target_end_id=xmi_id(end) or None,
                    owner=source,
                    xmi_id_=assoc_id or None,
                )
                owning_attribute.association = association
            else:
                if len(owned_ends) != 2:
                    raise ValueError(f"Association {assoc_id} needs two owned ends")
                target_end, source_end = owned_ends
                source = ref_obj(source_end, "type")
                target = ref_obj(target_end, "type")
                if not isinstance(source, Class):
                    raise TypeError(f"Association {assoc_id} source is not a class")
                target_lower, target_upper = parse_lower_upper(target_end)
                source_lower, source_upper = parse_lower_upper(source_end)
                association = DirectedAssociation(
                    name=el.get("name", ""),
                    source=source,
                    target=target,  # type: ignore[arg-type]
                    source_role=source_end.get("name", ""),
                    target_role=target_end.get("name", ""),
                    source_lower=source_lower,
                    source_upper=source_upper,
                    target_lower=target_lower,
                    target_upper=target_upper,
                    source_end_id=xmi_id(target_end) or None,
                    target_end_id=xmi_id(source_end) or None,
                    owner=source,
                    xmi_id_=assoc_id or None,
                )
            _to_insert.append((ns, idx, association))
            register(association)

        for ns, el, idx in dependency_elems:
            source = ref_obj(el, "client")
            target = ref_obj(el, "supplier")
            if isinstance(source, UnionType):
                source.members.append(target)  # type: ignore[arg-type]
            else:
                dependency = Dependency(
                    name=el.get("name") or f"Dependency{source.name}{target.name}",
                    source=source,  # type: ignore[arg-type]
                    target=target,  # type: ignore[arg-type]
                    owner=ns,
                    xmi_id_=xmi_id(el) or None,
                )
                _to_insert.append((ns, idx, dependency))
                register(dependency)

        for _, (ns, idx, obj) in sorted(
            enumerate(_to_insert),
            key=lambda t: (id(t[1][0]), -t[1][1], -t[0]),
        ):
            ns.elements.insert(idx, obj)

        model._reserve_ids_from_xml(root)
        return model

    def ref(self, el: ET.Element, name: str, other : Base):
        other_root = other
        while not isinstance(other_root, Model):
            other_root = other_root.owner
        if self is other_root:
            el.set(name, other.xmi_id)
        else:
            ET.SubElement(
                el,
                name,
                {
                    XMI.type: {
                        'cl': 'uml:Class',
                        'un': 'uml:Class',
                        'dt': 'uml:DataType',
                        'en': 'uml:Enumeration',
                    }[other.xmi_id.split('_')[0]],
                    "href": f"{other_root.name}.uml#{other.xmi_id}",
                },
            )

    def _namespace_element(self, ns: Namespace, parent: Optional[ET.Element] = None) -> ET.Element:
        if isinstance(ns, Model):
            el = ET.Element(
                UML.Model,
                {
                    XMI.id: ns.xmi_id,
                    "name": ns.name,
                    XMI.version: "20131001",
                },
            )
        else:
            assert parent is not None
            el = ET.SubElement(
                parent,
                "packagedElement",
                {
                    XMI.type: "uml:Package",
                    XMI.id: ns.xmi_id,
                    "name": ns.name,
                },
            )

        for elt in ns.elements:
            if isinstance(elt, Package):
                self._namespace_element(elt, parent=el)

            elif isinstance(elt, DataType):
                de = ET.SubElement(
                    el,
                    "packagedElement",
                    {
                        XMI.type: "uml:DataType",
                        XMI.id: elt.xmi_id,
                        "name": elt.name,
                    },
                )
                for ct in elt.constraints:
                    re = ET.SubElement(
                        de,
                        "ownedRule",
                        {
                            XMI.type: "uml:Constraint",
                            XMI.id: ct.xmi_id,
                            "name": ct.name,
                        },
                    )
                    spec = ET.SubElement(
                        re,
                        "specification",
                        {
                            XMI.type: "uml:OpaqueExpression",
                            XMI.id: f"{elt.xmi_id}_{ct.name}",
                            "name": ct.name,
                        },
                    )
                    lang = ET.SubElement(spec, "language")
                    lang.text = ct.language
                    body = ET.SubElement(spec, "body")
                    body.text = ct.body
                for sup in elt.super:
                    gen = ET.SubElement(
                        de,
                        "generalization",
                        {
                            XMI.type: "uml:Generalization",
                            XMI.id: f"{elt.xmi_id}_gen_{sup.name}",
                        },
                    )
                    self.ref(gen, "general", sup)
                
            elif isinstance(elt, UnionType):
                de = ET.SubElement(
                    el,
                    "packagedElement",
                    {
                        XMI.type: "uml:Class",
                        XMI.id: elt.xmi_id,
                        "name": elt.name,
                    },
                )
                for mem in elt.members:
                    attrs = {
                        XMI.type: "uml:Dependency",
                        XMI.id: f"dp_{elt.name}_{mem.name}",
                        # @todo verify order
                    }
                    dep = ET.SubElement(el, "packagedElement", attrs)
                    self.ref(dep, "client", elt)
                    self.ref(dep, "supplier", mem)

            elif isinstance(elt, Class):
                ce = ET.SubElement(
                    el,
                    "packagedElement",
                    {
                        XMI.type: "uml:Class",
                        XMI.id: elt.xmi_id,
                        "name": elt.name,
                        **({"isAbstract": "true"} if elt.is_abstract else {})
                    },
                )

                if elt.comment is not None:
                    c = ET.SubElement(
                        ce,
                        "ownedComment",
                        {
                            XMI.type: "uml:Comment",
                            XMI.id: f"{elt.xmi_id}_comment_0",
                        },
                    )
                    body = ET.SubElement(c, "body")
                    body.text = elt.comment

                for a in (c for c in elt.attributes if isinstance(c, Constraint)):
                    re = ET.SubElement(
                        ce,
                        "ownedRule",
                        {
                            XMI.type: "uml:Constraint",
                            XMI.id: a.xmi_id,
                            "name": a.name,
                        },
                    )
                    spec = ET.SubElement(
                        re,
                        "specification",
                        {
                            XMI.type: "uml:OpaqueExpression",
                            XMI.id: f"{elt.xmi_id}_{a.name}",
                            "name": a.name,
                        },
                    )
                    lang = ET.SubElement(spec, "language")
                    lang.text = a.language
                    body = ET.SubElement(spec, "body")
                    body.text = a.body

                for sup in elt.super:
                    gen = ET.SubElement(
                        ce,
                        "generalization",
                        {
                            XMI.type: "uml:Generalization",
                            XMI.id: f"{elt.xmi_id}_gen_{sup.name}",
                        },
                    )
                    self.ref(gen, "general", sup)

                for a in (c for c in elt.attributes if isinstance(c, Attribute)):
                    attrs = {
                        XMI.type: "uml:Property",
                        XMI.id: a.xmi_id,
                        "name": a.name,
                        **({"isOrdered": str(a.ordered).lower()} if a.ordered else {}),
                    }
                    ae = ET.SubElement(ce, "ownedAttribute", attrs)
                    
                    if a.derived is not None:
                        ae.set("isDerived", "true")

                    if a.comment is not None:
                        c = ET.SubElement(
                            ae,
                            "ownedComment",
                            {
                                XMI.type: "uml:Comment",
                                XMI.id: f"{a.xmi_id}_comment_0",
                            },
                        )
                        body = ET.SubElement(c, "body")
                        body.text = a.comment

                    self.ref(ae, "type", a.type)

                    if a.association is not None:
                        self.ref(ae, "association", a.association)

                    ET.SubElement(
                        ae,
                        "lowerValue",
                        {
                            XMI.type: "uml:LiteralInteger",
                            XMI.id: f"{a.xmi_id}_lower",
                            **({"value": str(a.lower)} if a.lower != 0 else {}),
                        },
                    )
                    ET.SubElement(
                        ae,
                        "upperValue",
                        {
                            XMI.type: "uml:LiteralUnlimitedNatural",
                            XMI.id: f"{a.xmi_id}_upper",
                            "value": upper_value(a.upper),
                        },
                    )
                    if a.derived is not None:
                        dv = ET.SubElement(
                            ae,
                            "defaultValue",
                            {
                                XMI.type: "uml:OpaqueExpression",
                                XMI.id: f"{a.xmi_id}_expr",
                                "name": a.name,
                            },
                        )
                        lang = ET.SubElement(dv, "language")
                        lang.text = "EXPRESS"
                        body = ET.SubElement(dv, "body")
                        body.text = a.derived
                    
            elif isinstance(elt, Enumeration):
                ee = ET.SubElement(
                    el,
                    "packagedElement",
                    {
                        XMI.type: "uml:Enumeration",
                        XMI.id: elt.xmi_id,
                        "name": elt.name,
                    },
                )
                for lit in elt.literals:
                    ET.SubElement(
                        ee,
                        "ownedLiteral",
                        {
                            XMI.type: "uml:EnumerationLiteral",
                            XMI.id: f"{elt.xmi_id}_{lit}",
                            "name": lit,
                        },
                    )

            elif isinstance(elt, Dependency):
                attrs = {
                    XMI.type: "uml:Dependency",
                    XMI.id: elt.xmi_id,
                    # @todo verify order
                }
                dep = ET.SubElement(el, "packagedElement", attrs)
                self.ref(dep, "client", elt.source)
                self.ref(dep, "supplier", elt.target)

            elif isinstance(elt, DirectedAssociation):
                member_ends = [
                    elt.owning_attribute.xmi_id if elt.owning_attribute is not None else elt.source_end_id,
                    elt.target_end_id]
                    
                packaged = ET.SubElement(
                    el,
                    "packagedElement",
                    {
                        XMI.type: "uml:Association",
                        XMI.id: elt.xmi_id,
                        "name": elt.name,
                        "memberEnd": " ".join(member_ends),
                    },
                )

                def write_end(id, name, type, lower, upper):
                    tgt = ET.SubElement(
                        packaged,
                        "ownedEnd",
                        {
                            XMI.type: "uml:Property",
                            XMI.id: id,
                            "name": name,
                        },
                    )
                    self.ref(tgt, "type", type)
                    tgt.set("association", elt.xmi_id)
                    ET.SubElement(
                        tgt,
                        "lowerValue",
                        {
                            XMI.type: "uml:LiteralInteger",
                            XMI.id: f"{id}_lower",
                            **({"value": str(lower)} if lower != 0 else {}),
                        },
                    )
                    ET.SubElement(
                        tgt,
                        "upperValue",
                        {
                            XMI.type: "uml:LiteralUnlimitedNatural",
                            XMI.id: f"{id}_upper",
                            "value": upper_value(upper),
                        },
                    )
                
                if elt.owning_attribute is None:
                    write_end(elt.source_end_id, elt.target_role, elt.target, elt.target_lower, elt.target_upper)    
                write_end(elt.target_end_id, elt.source_role, elt.source, elt.source_lower, elt.source_upper)

            elif isinstance(elt, Constraint):
                ce = ET.SubElement(
                    el,
                    "packagedElement",
                    {
                        XMI.type: "uml:Constraint",
                        XMI.id: elt.xmi_id,
                        "name": elt.name,
                    },
                )
                ET.SubElement(
                    ce,
                    "specification",
                    {
                        XMI.type: "uml:OpaqueExpression",
                        XMI.id: f"{elt.xmi_id}_expr",
                        "name": elt.name,
                    },
                )
                spec = ce[-1]
                lang = ET.SubElement(spec, "language")
                lang.text = elt.language
                body = ET.SubElement(spec, "body")
                body.text = elt.body
            elif isinstance(elt, Import):
                
                sel = ET.SubElement(
                    el,
                    "packageImport",
                    {
                        XMI.type: "uml:PackageImport",
                        XMI.id: elt.xmi_id,
                    },
                )

                assert "#" in elt.name

                ET.SubElement(
                    sel,
                    "importedPackage",
                    {
                        XMI.type: "uml:Package",
                        "href": elt.name,
                    },
                )
        return el

    def to_element(self) -> ET.Element:
        return self._namespace_element(self)

    def to_xml(self) -> str:
        raw = ET.tostring(self.to_element(), encoding="utf-8")
        return minidom.parseString(raw).toprettyxml(indent="  ", encoding="UTF-8").decode("utf-8")

    def save(self, path: str | Path | None = None) -> None:
        path = Path(path) if path is not None else Path(f"{self.name}.uml")
        xml = self.to_xml()
        tmp_path = path.with_name(f"{path.name}.tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            f.write(xml)
        tmp_path.replace(path)


class ModelSet:
    def __init__(self, models: Iterable[Model] | None = None):
        self.models: list[Model] = []
        if models is not None:
            self.extend(models)

    @classmethod
    def from_model(cls, model: Model) -> "ModelSet":
        return cls(model.referenced_models())

    @classmethod
    def load(cls, path: str | Path) -> "ModelSet":
        return cls.from_model(Model.load(path))

    def __iter__(self) -> Iterator[Model]:
        return iter(self.models)

    def __len__(self) -> int:
        return len(self.models)

    def __contains__(self, model: Model) -> bool:
        return any(existing is model for existing in self.models)

    def __getitem__(self, index: int) -> Model:
        return self.models[index]

    def add(self, model: Model) -> Model:
        if model not in self:
            self.models.append(model)
        return model

    def append(self, model: Model) -> None:
        self.add(model)

    def extend(self, models: Iterable[Model]) -> None:
        for model in models:
            self.add(model)

    def validate(self) -> list[tuple[Model, str]]:
        rendered = [(model, model.to_xml()) for model in self.models]
        roots: list[tuple[Model, str, ET.Element]] = []
        errors: list[str] = []

        for model, xml in rendered:
            file_name = f"{model.name}.uml"
            try:
                roots.append((model, file_name, ET.fromstring(xml)))
            except ET.ParseError as e:
                errors.append(f"{file_name}: invalid XML: {e}")

        if errors:
            raise ValueError("Invalid XMI model set:\n- " + "\n- ".join(errors))

        model_files: dict[str, Model] = {}
        ids_by_file: dict[str, dict[str, ET.Element]] = {}
        id_locations: dict[str, list[str]] = {}

        for model, file_name, root in roots:
            if file_name in model_files:
                errors.append(f"{file_name}: model file name is not unique")
            model_files[file_name] = model

            ids: dict[str, ET.Element] = {}
            for el in root.iter():
                ref_id = xmi_id(el)
                if not ref_id:
                    continue
                if ref_id in ids:
                    errors.append(f"{file_name}: duplicate xmi:id {ref_id!r}")
                ids.setdefault(ref_id, el)
                id_locations.setdefault(ref_id, []).append(file_name)
            ids_by_file[file_name] = ids

        for ref_id, locations in id_locations.items():
            if len(locations) > 1:
                errors.append(
                    f"xmi:id {ref_id!r} is not unique across the model set: "
                    + ", ".join(locations)
                )

        def element_label(el: ET.Element) -> str:
            parts = [local_name(el.tag)]
            if ref_id := xmi_id(el):
                parts.append(f"xmi:id={ref_id!r}")
            if name := el.get("name"):
                parts.append(f"name={name!r}")
            return " ".join(parts)

        def element_type(el: ET.Element) -> str:
            if value := xmi_type(el):
                return value
            tag = local_name(el.tag)
            return f"uml:{tag}" if tag == "Model" else ""

        def validate_direct_reference(
            source_file: str,
            el: ET.Element,
            attr_name: str,
            value: str,
        ) -> None:
            ref_ids = value.split()
            if not ref_ids:
                errors.append(
                    f"{source_file}: {element_label(el)} {attr_name}={value!r} "
                    "must contain at least one xmi:id reference"
                )
                return
            if attr_name not in MULTI_REFERENCE_ATTRIBUTES and len(ref_ids) != 1:
                errors.append(
                    f"{source_file}: {element_label(el)} {attr_name}={value!r} "
                    "must contain a single xmi:id reference"
                )
            for ref_id in ref_ids:
                if "#" in ref_id:
                    errors.append(
                        f"{source_file}: {element_label(el)} {attr_name}={ref_id!r} "
                        "must use an href child element for cross-model references"
                    )
                    continue
                if ref_id in ids_by_file[source_file]:
                    continue
                other_locations = [
                    location
                    for location in id_locations.get(ref_id, [])
                    if location != source_file
                ]
                if other_locations:
                    errors.append(
                        f"{source_file}: {element_label(el)} {attr_name}={ref_id!r} "
                        "references another model directly; use href with a # fragment "
                        f"to {', '.join(other_locations)}"
                    )
                else:
                    errors.append(
                        f"{source_file}: {element_label(el)} {attr_name}={ref_id!r} "
                        "does not resolve within the model set"
                    )

        def validate_href_reference(source_file: str, el: ET.Element, href: str) -> None:
            if "#" not in href:
                errors.append(
                    f"{source_file}: {element_label(el)} href={href!r} "
                    "must include a # fragment"
                )
                return

            target_file, ref_id = href.rsplit("#", 1)
            if not target_file or not ref_id:
                errors.append(
                    f"{source_file}: {element_label(el)} href={href!r} "
                    "must include both a model file and an xmi:id fragment"
                )
                return

            if target_file == source_file:
                errors.append(
                    f"{source_file}: {element_label(el)} href={href!r} "
                    "references the same model; use a direct xmi:id reference"
                )
                return

            target_ids = ids_by_file.get(target_file)
            if target_ids is None:
                errors.append(
                    f"{source_file}: {element_label(el)} href={href!r} "
                    f"references model file {target_file!r}, which is not in the model set"
                )
                return

            target = target_ids.get(ref_id)
            if target is None:
                if ref_id in ids_by_file[source_file]:
                    errors.append(
                        f"{source_file}: {element_label(el)} href={href!r} "
                        "references the same model; use a direct xmi:id reference"
                    )
                elif ref_id in id_locations:
                    errors.append(
                        f"{source_file}: {element_label(el)} href={href!r} "
                        f"points at the wrong model file; {ref_id!r} exists in "
                        + ", ".join(id_locations[ref_id])
                    )
                else:
                    errors.append(
                        f"{source_file}: {element_label(el)} href={href!r} "
                        "does not resolve within the model set"
                    )
                return

            expected_type = el.get(XMI.type)
            actual_type = element_type(target)
            if expected_type and actual_type and expected_type != actual_type:
                errors.append(
                    f"{source_file}: {element_label(el)} href={href!r} has "
                    f"xmi:type {expected_type!r}, but target {ref_id!r} is {actual_type!r}"
                )

        for _, file_name, root in roots:
            for el in root.iter():
                for attr_name, value in el.attrib.items():
                    if attr_name in REFERENCE_ATTRIBUTES | MULTI_REFERENCE_ATTRIBUTES:
                        validate_direct_reference(file_name, el, attr_name, value)
                    elif attr_name == XMI.idref:
                        validate_direct_reference(file_name, el, "xmi:idref", value)

                if local_name(el.tag) not in REFERENCE_ELEMENTS:
                    continue
                if href := el.get("href"):
                    validate_href_reference(file_name, el, href)
                if el.get("href") is None and el.get(XMI.idref) is None:
                    errors.append(
                        f"{file_name}: {element_label(el)} is a reference element "
                        "but has neither href nor xmi:idref"
                    )

        if errors:
            raise ValueError("Invalid XMI model set:\n- " + "\n- ".join(errors))

        return rendered

    def save(self, directory: str | Path | None = None) -> None:
        rendered = self.validate()
        directory = Path(directory) if directory is not None else None
        for model, xml in rendered:
            path = (
                directory / f"{model.name}.uml"
                if directory is not None
                else Path(f"{model.name}.uml")
            )
            tmp_path = path.with_name(f"{path.name}.tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                f.write(xml)
            tmp_path.replace(path)
