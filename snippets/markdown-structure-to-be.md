```
docs_root/schemas/<layer>/<schema>/Entities/<Entity>.md
  unchanged:
  entity/type descriptions and entity attributes still come from UML + ifc-spec-dir DocEntity/Documentation.md

docs_root/schemas/<layer>/<schema>/PropertySets/<Pset>.md
  # <Pset>                           [UML class or XLSX pset name]
  short definition                   [ifc-spec-dir/.../PropertySets/<Pset>/Documentation.md]
  ## Comments                        [only if XLSX local extension exists]
    ### <Property>                   [XLSX col A]
      text                           [XLSX col E]

docs_root/schemas/<layer>/<schema>/QuantitySets/<Qto>.md
  # <Qto>                            [UML class or XLSX qto name]
  short definition                   [ifc-spec-dir/.../QuantitySets/<Qto>/Documentation.md]
  ## Comments                        [only if XLSX local extension exists]
    ### <Quantity>                   [XLSX col A]
      text                           [XLSX col E]

docs_root/properties/<letter>/<Name>.md
  <Name>
  ======
  text                               [XLSX "1 - For initial upload" col D]

docs_root/schemas/<resolved-layer>/<resolved-schema>/PropertyEnumerations/<PEnum>.md
  # <PEnum>                          [XLSX "1 - PENUMs..." Penum Name]
  ## Items
    ### <Value>                      [XLSX Original Values]
      text                           [XLSX Original Description]
```
