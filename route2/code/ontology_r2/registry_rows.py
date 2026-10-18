"""Compile shared registry definitions and retain every scoped source binding.

Tenant/business scope locates a registry row; it is not the identity of the
shared definition. Equal structural definitions share one type and instance,
while row alignments retain local codes, evidence and joint reference scopes.
"""
from __future__ import annotations

from collections import defaultdict

from .models import DerivedType, SourceProperty
from .registry_identity import canonicalize, describe_entry, text
from .storage import digest, qi, record_evidence_id


_OPERATORS = {"sum": "sum", "avg": "avg", "average": "avg", "count": "count",
              "distinct_count": "distinct_count", "min": "min", "max": "max", "filter": "filter"}


def compile_registry_rows(data, plan, *, max_rows=20000):
    """Compile definitions once, then map every source row to its shared object."""
    entries, omitted = [], 0
    for name, info in sorted(getattr(data, "tables", {}).items()):
        spec = _registry_spec(info)
        if spec is None:
            continue
        rows = list(_rows(data, info, max_rows + 1))
        if len(rows) > max_rows:
            omitted += max(1, info.get("rows", len(rows)) - max_rows)
        for row in rows[:max_rows]:
            if not text(row.get(spec[1])) or not text(row.get(spec[2])):
                continue
            entries.append(describe_entry(name, info, row, spec, data.record_id(name, row)))
        info["structural_rows_compiled"] = True
    groups, row_types, formula_signatures, formula_bindings = canonicalize(entries)
    concepts, alignments, dimension_types = [], [], {}
    existing = {item.id: index for index, item in enumerate(plan.object_types)}
    record_scopes = {}
    for type_id, members in sorted(groups.items()):
        members.sort(key=lambda item: (item["table"], item["record_id"]))
        first = members[0]
        concept_id = "concept:" + type_id[len("type:"):]
        source_refs, evidence, properties, reference_scopes = [], set(), defaultdict(set), {}
        for entry in members:
            name, row, rid = entry["table"], entry["row"], entry["record_id"]
            field_roles = {entry["name_column"]: "name", entry["code_column"]: "identity"}
            field_roles.update({column: ("metadata" if role == "operator" else role)
                                for column, role in entry["fields"].items()})
            field_roles.update({column: "description" for column in entry["description_columns"]})
            field_roles.update({column: "metadata" for column in entry["reference_scope"]
                                if column != "source_table"})
            for column, role in field_roles.items():
                if not text(row.get(column)):
                    continue
                eid = _proof(data, name, row, rid, column)
                properties[(role, name, column)].add(eid)
                evidence.add(eid)
            scope = entry["reference_scope"]
            reference_scopes[digest(scope)] = scope
            record_scopes[rid] = scope
            source_refs.append({"record_id": rid, "table": name, "row": row["__r2_row"],
                                "card_id": None, "scope": "structural_registry_row"})
            alignments.append({
                "id": "alignment:structural:" + rid, "source_record_id": rid,
                "source_card_id": None, "concept_id": concept_id, "mapping_kind": "exact",
                "scope": entry["identity"], "reference_scope": scope,
                "source_code": entry["code"],
                "evidence_ids": [record_evidence_id(rid, entry["name_column"])],
                "decision": "accepted_by_structural_registry"})
            if entry["role"] == "Dimension":
                dimension_types[(name, tuple(sorted(entry["identity"].items())), entry["code"])] = type_id
        source_properties = [SourceProperty(role=role, source_table=table, source_column=column,
                                           evidence_ids=sorted(ids))
                             for (role, table, column), ids in sorted(properties.items())]
        descriptions = sorted({entry["description_text"] for entry in members if entry["description_text"]})
        label = min(entry["label"] for entry in members)
        definition = descriptions[0] if descriptions else label
        parameters = {key: value for key, value in first["attributes"].items()
                      if value and key not in ("unit", "operator")}
        scopes = [reference_scopes[key] for key in sorted(reference_scopes)]
        unit = first["attributes"].get("unit") or None
        operator = _OPERATORS.get(first["attributes"].get("operator", ""))
        item = DerivedType(
            id=type_id, parent=first["role"], label=label, definition=definition,
            evidence_ids=sorted(evidence), category="business_type", identity_qualifiers={},
            reference_scopes=scopes, definition_parameters=parameters,
            unit=unit, aggregation_operator=operator, source_properties=source_properties,
            derivation_kind="exact_definition",
            evidence_scope="multiple_definition_records" if len(members) > 1 else "definition_record")
        if type_id in existing:
            plan.object_types[existing[type_id]] = item
        else:
            plan.object_types.append(item)
        concepts.append({
            "id": concept_id, "type": first["role"], "label": label, "proposed_label": label,
            "definition": definition, "definition_basis": "structural_registry_row",
            "classification_evidence": {"basis": "schema_typed",
                "registry_tables": sorted({entry["table"] for entry in members})},
            "identity_scope": "shared_definition", "ontology_level": "type",
            "ontology_type_id": type_id, "scope": {}, "applicability_scope": {},
            "reference_scopes": scopes, "definition_parameters": parameters, "parameter_evidence": {},
            "identity_qualifiers": {}, "classification_attributes": {}, "scope_roles": {},
            "unrestricted_scope": {}, "observation_coordinates": {}, "unclassified_scope": {},
            "unit": unit, "aggregation_operator": operator,
            "source_properties": [prop.model_dump() for prop in source_properties],
            "source_refs": source_refs, "evidence_ids": sorted(evidence),
            "decision": "accepted_by_structural_registry"})
    data._registry_row_types = row_types
    data._registry_record_to_object = {rid: "registry_object:" + type_id[len("type:"):]
                                       for rid, type_id in row_types.items()}
    data._registry_reference_scopes = record_scopes
    data._registry_formula_signatures = formula_signatures
    data._registry_formula_bindings = formula_bindings
    data._dimension_row_types = dimension_types
    return {"concepts": concepts, "record_alignments": alignments,
            "coverage": {"registry_types": len(concepts), "source_registry_rows": len(entries),
                         "shared_definition_rows": len(entries) - len(concepts),
                         "rows_omitted": omitted, "partial": omitted > 0}}


def _registry_spec(info):
    quantity = info.get("quantity_registry") or {}
    if quantity.get("role") in ("Measure", "Metric"):
        return (quantity["role"], quantity["code_column"], quantity["name_column"],
                list(quantity.get("scope_columns") or []))
    dimension = info.get("dimension_registry") or {}
    if dimension.get("code_column") and dimension.get("name_column"):
        return ("Dimension", dimension["code_column"], dimension["name_column"],
                list(dimension.get("scope_columns") or []))
    return None


def _rows(data, info, limit):
    cursor = data.db.execute(
        f"SELECT * FROM {qi(info['sql_name'])} ORDER BY __r2_row LIMIT ?", [limit])
    names = [column[0] for column in cursor.description]
    for values in cursor.fetchall():
        yield dict(zip(names, values))


def _proof(data, table, row, record_id, column):
    eid = record_evidence_id(record_id, column)
    data.evidence[eid] = {
        "id": eid, "origin": "observed_record", "raw_fragment": str(row[column]),
        "raw_fragment_truncated": False,
        "source_ref": {"table": table, "record_id": record_id, "row": row["__r2_row"],
                       "column": column, "snapshot_id": data.snapshot_id}}
    return eid
