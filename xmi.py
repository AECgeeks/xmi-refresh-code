from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count
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

_ids = count(1)

def new_id(prefix: str) -> str:
    return f"{prefix}_{next(_ids)}"

def upper_value(v: int | None) -> str:
    return "*" if v is None else str(v)

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
        return f"at_{obj.owner.name}_{slug(obj.name)}"
    if isinstance(obj, DirectedAssociation):
        if '_' in slug(obj.name):
            return f"as_{slug(obj.name)}"
        else:
            return f"as_{obj.source.name}_{slug(obj.name)}"
    if isinstance(obj, UnionType):
        return f"un_{slug(obj.name)}"
    if isinstance(obj, Constraint):
        if not obj.owner.xmi_id.startswith("pk_"):
            own = obj.owner.name + "_"
        else:
            own = ""
        return f"ct_{own}{slug(obj.name)}"
    if isinstance(obj, Import):
        return f"ip_{obj.name.split('.')[0]}"
    breakpoint()
    return ""

@dataclass(kw_only=True)
class Base:
    xmi_id_: Optional[str] = None
    owner: 'Optional[Base]' = None

    @property
    def xmi_id(self) -> str:
        if i := self.xmi_id_:
            return i
        self.xmi_id_ = generate_id(self)
        return self.xmi_id_

@dataclass(eq=False)
class Import(Base):
    name: str

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
    source_end_id: str = field(default_factory=lambda: new_id("end"))
    target_end_id: str = field(default_factory=lambda: new_id("end"))


@dataclass(eq=False)
class Class(Base):
    name: str
    parent: Namespace
    attributes: list[Attribute | Constraint] = field(default_factory=list)
    super: list[Class] = field(default_factory=list)
    comment: Optional[str] = None
    is_abstract: bool = False

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
        self.parent.elements.append(assoc)
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
            self.attributes.append(attr)           
        return assoc


@dataclass(eq=False)
class Namespace(Base):
    name: str
    elements: list[Base] = field(default_factory=list)

    def datatype(self, name: str, super: 'Optional[DataType]' = None) -> DataType:
        t = DataType(name=name, super=[super] if super else [], owner=self)
        self.elements.append(t)
        return t

    def cls(self, name: str, super: Class | None = None, comment: Optional[str] = None, is_abstract: bool = False) -> Class:
        c = Class(name=name, super=[super] if super else [], parent=self, comment=comment, owner=self, is_abstract=is_abstract)
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

    def _namespace_element(self, parent: ET.Element, ns: Namespace) -> ET.Element:
        if isinstance(ns, Model):
            el = ET.SubElement(
                parent,
                UML.Model,
                {
                    XMI.id: ns.xmi_id,
                    "name": ns.name,
                },
            )
        else:
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
                self._namespace_element(el, elt)

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
                for sup in elt.super:
                    gen = ET.SubElement(
                        de,
                        "generalization",
                        {
                            XMI.type: "uml:Generalization",
                            XMI.id: new_id("gen"),
                        },
                    )
                    self.ref(gen, "general", sup)
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
                            XMI.id: new_id("expr"),
                            "name": ct.name,
                        },
                    )
                    lang = ET.SubElement(spec, "language")
                    lang.text = ct.language
                    body = ET.SubElement(spec, "body")
                    body.text = ct.body
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
                        XMI.id: new_id("dependency"),
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
                            XMI.id: new_id("comment"),
                        },
                    )
                    body = ET.SubElement(c, "body")
                    body.text = elt.comment

                for sup in elt.super:
                    gen = ET.SubElement(
                        ce,
                        "generalization",
                        {
                            XMI.type: "uml:Generalization",
                            XMI.id: new_id("gen"),
                        },
                    )
                    self.ref(gen, "general", sup)

                for a in elt.attributes:
                    if isinstance(a, Attribute):
                        attrs = {
                            XMI.type: "uml:Property",
                            XMI.id: a.xmi_id,
                            "name": a.name,
                            "isOrdered": str(a.ordered).lower(),
                        }
                        if a.derived is not None:
                            attrs["isDerived"] = "true"
                        ae = ET.SubElement(ce, "ownedAttribute", attrs)
                        if a.association is not None:
                            self.ref(ae, "association", a.association)
                        self.ref(ae, "type", a.type)
                        ET.SubElement(
                            ae,
                            "lowerValue",
                            {
                                XMI.type: "uml:LiteralInteger",
                                XMI.id: new_id("lower"),
                                "value": str(a.lower),
                            },
                        )
                        ET.SubElement(
                            ae,
                            "upperValue",
                            {
                                XMI.type: "uml:LiteralUnlimitedNatural",
                                XMI.id: new_id("upper"),
                                "value": upper_value(a.upper),
                            },
                        )
                        if a.derived is not None:
                            dv = ET.SubElement(
                                ae,
                                "defaultValue",
                                {
                                    XMI.type: "uml:OpaqueExpression",
                                    XMI.id: new_id("expr"),
                                    "name": a.name,
                                },
                            )
                            lang = ET.SubElement(dv, "language")
                            lang.text = "EXPRESS"
                            body = ET.SubElement(dv, "body")
                            body.text = a.derived
                        if a.comment is not None:
                            c = ET.SubElement(
                                ae,
                                "ownedComment",
                                {
                                    XMI.type: "uml:Comment",
                                    XMI.id: new_id("comment"),
                                },
                            )
                            body = ET.SubElement(c, "body")
                            body.text = a.comment
                    elif isinstance(a, Constraint):
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
                                XMI.id: new_id("expr"),
                                "name": a.name,
                            },
                        )
                        lang = ET.SubElement(spec, "language")
                        lang.text = a.language
                        body = ET.SubElement(spec, "body")
                        body.text = a.body

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
                            XMI.id: new_id("literal"),
                            "name": lit,
                        },
                    )
            
            elif isinstance(elt, Dependency):
                attrs = {
                    XMI.type: "uml:Dependency",
                    XMI.id: new_id("dependency"),
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
                            "association": elt.xmi_id,
                        },
                    )
                    self.ref(tgt, "type", type)
                    ET.SubElement(
                        tgt,
                        "lowerValue",
                        {
                            XMI.type: "uml:LiteralInteger",
                            XMI.id: new_id("lower"),
                            "value": str(lower),
                        },
                    )
                    ET.SubElement(
                        tgt,
                        "upperValue",
                        {
                            XMI.type: "uml:LiteralUnlimitedNatural",
                            XMI.id: new_id("upper"),
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
                        XMI.id: new_id("expr"),
                        "name": elt.name,
                    },
                )
                spec = ce[-1]
                lang = ET.SubElement(spec, "language")
                lang.text = elt.language
                body = ET.SubElement(spec, "body")
                body.text = elt.body
            elif isinstance(elt, Import):
                
                ET.SubElement(
                    el,
                    "packageImport",
                    {
                        XMI.type: "uml:PackageImport",
                        XMI.id: elt.xmi_id,
                        "importedPackage": elt.name,
                    },
                )
        return el

    def to_element(self) -> ET.Element:
        root = ET.Element(
            XMI.XMI,
            {
                XMI.version: "20131001",
            },
        )
        self._namespace_element(root, self)
        return root

    def to_xml(self) -> str:
        raw = ET.tostring(self.to_element(), encoding="utf-8")
        return minidom.parseString(raw).toprettyxml(indent="  ", encoding="UTF-8").decode("utf-8")

    def save(self) -> None:
        with open(f"{self.name}.uml", "w", encoding="utf-8") as f:
            f.write(self.to_xml())

if __name__ == "__main__":
    m = Model("Example")

    string = m.datatype("String")
    integer = m.datatype("Integer")

    person = m.cls("Person")
    employee = m.cls("Employee", super=person)
    company = m.cls("Company")

    person.attr("name", string, lower=1)
    employee.attr("employeeId", integer, lower=1)

    company.directed_association(
        name="Employment",
        target=employee,
        source_role="employer",
        target_role="employees",
        source_lower=1,
        source_upper=1,
        target_lower=0,
        target_upper=None,
        ordered=True,
    )

    m.save("example.uml")