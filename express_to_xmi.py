from collections import defaultdict
from pathlib import Path
from typing import Mapping, Optional

import ifcopenshell
import toposort
import re

import psd
import xmi
import xmi_parse

w = ifcopenshell.ifcopenshell_wrapper

def attribute_ref(a):
    if isinstance(a, w.named_type):
        return a.declared_type().name()
    elif isinstance(a, w.aggregation_type):
        return attribute_ref(a.type_of_element())
    elif isinstance(a, w.simple_type):
        return a.declared_type()
    else:
        breakpoint()

def convert_express_to_xmi(schema_name, schema_source, pset_dir, original_xmi):
    # @todo unique, optional aggregates also inverse
    # @todo make sure psets are only associated to object descendants

    orig_xmi = xmi_parse.parse_xmi(original_xmi)
    package_paths = {n.name: n.package_path for n in orig_xmi.package_hierarchy}
    created = {}

    s = w.schema_by_name(schema_name)
    
    M = xmi.Model(schema_name)
    models = [M]
    def pkg(item_name : str):
        c = M
        for depth, pk in enumerate(package_paths[item_name]):
            if pk.upper() == schema_name.upper():
                c = M
            elif el := created.get(pk):
                c = el
            else:
                if depth == 1:
                    models.append(xmi.Model(pk))
                    c = models[-1]
                    M.elements.append(xmi.Import(f"{pk}.uml#pk_{pk}", owner=M))
                c = created[pk] = c.package(pk)
        return c
    base = M.package("base")

    lines = list(open(schema_source).readlines())
    functions, rules = (defaultdict(list) for _ in range(2))
    for i, [name, *_] in filter(lambda t: t[1], [(i, re.findall(r'\bFUNCTION\s*(\w+)', ln)) for i, ln in enumerate(lines)]):
        while True:
            functions[name].append(lines[i])
            if re.findall(r'\bEND_FUNCTION\s*;', lines[i]):
                break
            i += 1

    for i, [name, *_] in filter(lambda t: t[1], [(i, re.findall(r'\bRULE\s*(\w+)\s*FOR\b', ln)) for i, ln in enumerate(lines)]):
        while True:
            rules[name].append(lines[i])
            if re.findall(r'\bEND_RULE\s*;', lines[i]):
                break
            i += 1

    unique_aggregates = {}
    derived = defaultdict(list)
    wheres = defaultdict(list)
    uniques = defaultdict(list)
    in_derive = False
    in_where = False
    in_unique = False
    entity = None
    for i, l in enumerate(lines):
        if ent := re.findall(r'\bENTITY\s*(\w+)', l):
            entity = ent[0]
        elif ent := re.findall(r'\bTYPE\s*(\w+)\s*=', l):
            entity = ent[0]
        elif re.findall(r'\bEND_ENTITY\s*;', l) or re.findall(r'\bEND_TYPE\s*;', l):
            entity = None
            in_derive, in_where, in_unique = False, False, False
        elif re.findall(r'\bDERIVE\b', l):
            in_derive, in_where, in_unique = True, False, False
        elif re.findall(r'\bUNIQUE\b', l) and not re.findall(r'\bOF\s+UNIQUE\b', l):
            in_derive, in_where, in_unique = False, False, True
        elif re.findall(r'\bWHERE\b', l):
            in_derive, in_where, in_unique = False, True, False
        elif entity is not None and in_derive:
            derived[entity].append(l)
        elif entity is not None and in_where:
            wheres[entity].append(l)
        elif entity is not None and in_unique:
            uniques[entity].append(l)
        elif entity is not None and re.findall(r'LIST \[\d+:(\d+|\?)\] OF UNIQUE', l):
            attr_name = l.split(':', 1)[0].strip()
            level = re.split(r'\s+', l[:l.index("OF UNIQUE")]).count("OF")
            unique_aggregates[(entity, attr_name)] = level

    def aggregation_suffix(a, for_inverse=False, attr: Optional[tuple] = None, nesting_depth=0):
        if isinstance(a, w.aggregation_type) or for_inverse:
            t = (("B","S",None) if for_inverse else "ABLS")[a.type_of_aggregation()]
            if t is None:
                return ""
            if attr is not None and attr in unique_aggregates and unique_aggregates[attr] == nesting_depth:
                t += "U"
            l = "?" if a.bound1() == -1 else a.bound1()
            u = "?" if a.bound2() == -1 else a.bound2()
            return f"_{t}[{l}:{u}]" + ("" if for_inverse else aggregation_suffix(a.type_of_element(), nesting_depth=nesting_depth+1, attr=attr))
        else:
            return ""


    for i, di in enumerate((functions, rules)):
        for k, lns in di.items():
            pkg(k).constraint(
                name=k,
                language="EXPRESS_FUNCTION" if i == 0 else "EXPRESS_RULE",
                body="".join(lns)
            )

    def get_deps(decl):
        if isinstance(decl, w.entity):
            # don't add attributes because we get into circular deps
            return [*((decl.supertype().name(),) if decl.supertype() else ())]
        elif isinstance(decl, w.type_declaration):
            ty = decl.declared_type()
            while isinstance(ty, w.aggregation_type):
                ty = ty.type_of_element()
            if ty and not isinstance(ty, w.simple_type):
                if isinstance(ty, w.named_type):
                    return [ty.declared_type().name()]
            else:
                print(ty)
                return []
        elif isinstance(decl, w.enumeration_type):
            return []
        elif isinstance(decl, w.select_type):
            return [x.name() for x in decl.select_list()]
        else:
            breakpoint()

    def break_none(decl, deps):
        assert None not in deps
        return deps

    deps = {
        decl.name(): break_none(decl, get_deps(decl)) for decl in s.declarations() 
    }
    ents = toposort.toposort_flatten(deps)

    def name_of(decl):
        while isinstance(decl, w.aggregation_type):
            decl = decl.type_of_element()
        if isinstance(decl, (w.type_declaration, w.entity)):
            return decl.name()
        elif isinstance(decl, w.simple_type):
            return decl.declared_type()
        elif isinstance(decl, w.named_type):
            return name_of(decl.declared_type())
        else:
            breakpoint()

    for simple in ("binary", "boolean", "integer", "logical", "number", "real", "string"):
        created[simple] = base.datatype(simple)
    created["PropertyTableValueBase"] = base.cls("PropertyTableValueBase")

    for e in map(s.declaration_by_name, ents):
        print(e.name())
        if isinstance(e, w.entity):
            sup = created[e.supertype().name()] if e.supertype() else None
            created[e.name()] = pkg(e.name()).cls(e.name(), sup, is_abstract=e.is_abstract())
        elif isinstance(e, w.enumeration_type):
            created[e.name()] = pkg(e.name()).enumeration(e.name(), e.enumeration_items())
        elif isinstance(e, w.type_declaration):
            created[e.name()] = pkg(e.name()).datatype(f"{e.name()}{aggregation_suffix(e.declared_type())}", created[name_of(e.declared_type())])
            if strlen := next(filter(None, [re.findall(r'STRING\s*\((\d+)\)\s*(FIXED)?', l) for l in lines if e.name() in l]), None):
                length, fixed = strlen[0]
                dt : xmi.DataType = created[e.name()]
                op = "=" if fixed else "<="
                dt.constraints.append(xmi.Constraint('strlen', 'OCL', f'self.size() {op} {length}', owner=created[e.name()]))
        elif isinstance(e, w.select_type):
            created[e.name()] = pkg(e.name()).union(e.name(), [created[x.name()] for x in e.select_list()])
        else:
            breakpoint()

    processed_symmetric_inverses = set()

    for e in s.entities():
        ecl : xmi.Class = created[e.name()]
        for a in e.attributes():
            target = created[attribute_ref(a.type_of_attribute())]
            attr_name = f"{a.name()}{aggregation_suffix(a.type_of_attribute(), attr=(e.name(), a.name()))}"
            if isinstance(target, (xmi.Class, xmi.UnionType)):
                orig_target_type = target_type = s.declaration_by_name(attribute_ref(a.type_of_attribute()))
                inv_name = ""
                if isinstance(target, xmi.Class):
                    # assert symmetric, union
                    for inv in target_type.all_inverse_attributes():
                        if inv.entity_reference().name() == e.name() and inv.attribute_reference().name() == a.name():
                            while target_type.supertype() and inv.name() in (i.name() for i in target_type.supertype().all_inverse_attributes()):
                                target_type = target_type.supertype()
                            if orig_target_type is target_type:
                                # assert symmetric, not sub-super-type distinction
                                inv_name = f"INV_{inv.name()}{aggregation_suffix(inv, for_inverse=True)}"
                                processed_symmetric_inverses.add((target_type.name(), inv.name()))
                ecl.directed_association(
                    a.name(),
                    target,
                    inv_name,
                    attr_name,
                    target_lower=0 if a.optional() else 1,
                    target_upper=1
                )
            else:
                ecl.attr(
                    attr_name,
                    target,
                    lower=0 if a.optional() else 1,
                    upper=1
                )
            print(' ', a.name(), a.type_of_attribute())
        if ptype_attr := next((a for a in e.attributes() if a.name() == "PredefinedType"), None):
            enum = ptype_attr.type_of_attribute().declared_type()
            for enum_lit in enum.enumeration_items():
                nm = f"{enum.name()}.{enum_lit}"
                if el := created.get(nm):
                    el.super.append(created[e.name()])
                else:
                    created[nm] = pkg(e.name()).cls(nm, created[e.name()])

    for e in s.entities():
        num_parent_inverses = len(e.supertype().all_inverse_attributes()) if e.supertype() else 0
        for inv in e.all_inverse_attributes()[num_parent_inverses:]:
            if (e.name(), inv.name()) not in processed_symmetric_inverses:
                inv_name = f"INV_{inv.name()}{aggregation_suffix(inv, for_inverse=True)}"
                opp : xmi.Class = created[inv.entity_reference().name()]
                opp.directed_association(e.name() + "_" + inv.name(), created[e.name()], inv_name, f"({inv.attribute_reference().name()})", owned_attribute=False)
                print(' ', e.name(), inv)

    for ent, vs in derived.items():
        for nm, ty, stmt in re.findall(r"(\w+)\s*:\s*(.+?)\s*:=\s*(.+?)\s*;", "".join(vs), re.DOTALL):
            print(ent, nm, ty, stmt)
            created[ent].attr(nm, created[ty.split(" ")[-1]], derived=" := ".join((ty, stmt)))

    for i, dict in enumerate((wheres, uniques)):
        for ent, vs in dict.items():
            for nm, body in re.findall(r"(\w+)\s*:\s*(.+?)\s*;", "".join(vs), re.DOTALL):
                if nm in ("CorrectPredefinedType", "CorrectTypeAssigned"):
                    # these are automatically generated
                    # except for some exceptions :)
                    if "EventTriggerType" not in body:
                        continue
                created[ent].rule(nm, body, language="EXPRESS_WHERE" if i == 0 else "EXPRESS_UNIQUE")

    for fn in Path(pset_dir).glob("*.xml"):
        def format_type(ty) -> tuple[xmi.TypeRef, int, int | None, Mapping]:
            if isinstance(ty, psd.PropertyDef):
                return format_type(ty.property_type)
            elif isinstance(ty, psd.QtoDef):
                return created[{
                    "Q_AREA": "IfcAreaMeasure",
                    "Q_COUNT": "IfcCountMeasure",
                    "Q_LENGTH": "IfcLengthMeasure",
                    "Q_NUMBER": "IfcQuantityNumber",
                    "Q_TIME": "IfcQuantityTime",
                    "Q_VOLUME": "IfcQuantityVolume",
                    "Q_WEIGHT": "IfcQuantityWeight",
                }[ty.qto_type]], 1, 1, {}
            elif isinstance(ty, psd.PropertySingle):
                return created[ty.data_type], 0, 1, {}
            elif isinstance(ty, psd.PropertyList):
                return created[ty.data_type], 1, None, {}
            elif isinstance(ty, psd.PropertyBounded):
                return created[ty.data_type], 0, 1, {"comment": "bounded"}
            elif isinstance(ty, psd.PropertyEnum):
                if en := created.get(ty.enum_name):
                    pass
                else:
                    en = created[ty.enum_name] = pkg(ty.enum_name).enumeration(ty.enum_name, ty.enum_items)
                return en, 1, None, {}
            elif isinstance(ty, psd.PropertyReference):
                return created[ty.ref_type], 1, 1, {}
            elif isinstance(ty, psd.PropertyTable):
                n = ty.defining + ty.defined
                if el := created.get(n):
                    pass
                else:
                    el = created[n] = pkg(pdef.name).cls(n, created["PropertyTableValueBase"])
                    el.attr("DefiningValue", created[ty.defining], 1, None)
                    el.attr("DefinedValue", created[ty.defined], 1, None)
                return el, 1, None, {}
            else:
                raise TypeError()
            
        pdef = psd.parse(open(fn, encoding='utf-8').read())
        created[pdef.name] = cls = pkg(pdef.name).cls(pdef.name, comment=pdef.template_type)
        for itm in pdef.items:
            *pos_args, kwargs = format_type(itm)
            cls.attr(itm.name, *pos_args, **kwargs)

    for k, vs in orig_xmi.pset_or_qto_associations.items():
        for v in vs:
            pkg(k).dependency(created[k], created[v])

    for depr in orig_xmi.deprecated_entities:
        x : xmi.Class = created[depr]
        x.comment = "DEPRECATED"

    return models

if __name__ == "__main__":
    ms = convert_express_to_xmi(
        'ifc4x3_add2',
        r'C:\Users\tkrij\Documents\AECgeeks\projects\ifcopenshell\v0.8\src\ifcopenshell-python\ifcopenshell\express\IFC4X3_ADD2.exp',
        r"..\IFC4.3.x-development\code\psd\\",
        r"..\IFC4.3.x-development\schemas\IFC.xml",
    )
    for m in ms:
        m.save()
