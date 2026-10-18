"""Relations and registry rows that follow from table roles, without a model call.

A member namespace points at its dimension. A code column points at a quantity
or dimension registry when nearly all of its values resolve to exactly one
registry row and the column name matches. Each check carries the registry's
scope columns, so a code repeated per tenant resolves inside its own tenant.
Registry rows reference shared definition types; table names never do.
"""

from __future__ import annotations

import json
import re

from .models import Condition, DerivedType, RelationPlan
from .storage import digest, qi, record_evidence_id


def structural_pair(data, bundle):
    """True only when this column pair is already a compiled structural link."""
    rule = bundle.get("rule") or {}
    source = rule.get("source") or {}
    target = rule.get("target") or {}
    pair = (source.get("table"), source.get("field"), target.get("table"), target.get("field"))
    if not all(pair):
        return False
    cached = getattr(data, "_structural_pairs", None)
    if cached is None:
        cached = {(item["source_table"], item["source_column"], item["target_table"], item["target_column"])
                  for item in _relations(data)}
        try:
            data._structural_pairs = cached
        except Exception:
            pass
    return pair in cached


def attach_structural_relations(data, plan, profile):
    """Add shared definitions and scoped points_to plans; keep the original on failure."""
    from .registry_rows import compile_registry_rows
    from .validation import validate_plan

    original = plan
    candidate = plan.model_copy(deep=True)
    added, compiled_links = [], []
    table_plans = {item.table: item for item in candidate.tables}
    _ensure_schema_evidence(data)
    registry = compile_registry_rows(data, candidate)
    class_tables = {}
    for relation in _relations(data):
        if relation["source_table"] not in table_plans or relation["target_table"] not in table_plans:
            continue
        evidence_id = _structural_evidence(data, relation)
        relation["structural_evidence_id"] = evidence_id
        compiled_links.append(relation)
        if not any(item.id == relation["plan_id"] for item in candidate.relations):
            selector = relation.get("selector")
            candidate.relations.append(RelationPlan(
                id=relation["plan_id"], source_table=relation["source_table"],
                target_table=relation["target_table"], mode="identifier",
                source_column=relation["source_column"], target_column=relation["target_column"],
                source_path=relation.get("source_path", []),
                scope_bindings=relation["scope_bindings"],
                selector=(Condition(op="in", field=selector["field"], values=selector["values"])
                          if selector else None),
                predicate=relation["parent"], semantics="reference",
                evidence_ids=[*relation["evidence_ids"], evidence_id]))
            added.append(relation)
        target_info = data.tables.get(relation["target_table"]) or {}
        if (relation.get("class_label") and relation["target_table"] not in class_tables
                and not target_info.get("structural_rows_compiled")
                and not target_info.get("member_table")):
            class_tables[relation["target_table"]] = _class_type(
                candidate, relation["target_table"], relation["class_label"], evidence_id)
    data._class_tables = class_tables
    type_relations, assertions = _type_relations(data, candidate, compiled_links)
    candidate.relation_types.extend(item for item in type_relations
                                    if item.id not in {known.id for known in candidate.relation_types})
    column_links = _column_links(data)
    errors = validate_plan(candidate, data, profile)
    if errors:
        data._registry_row_types, data._dimension_row_types, data._class_tables = {}, {}, {}
        data._registry_record_to_object, data._registry_reference_scopes = {}, {}
        data._registry_formula_signatures, data._registry_formula_bindings = {}, {}
        return original, {"attached": 0, "skipped": len(added), "links": [],
                          "column_links": [], "concepts": [], "record_alignments": [],
                          "assertions": [], "registry": registry["coverage"], "errors": errors[:8]}
    return candidate, {"attached": len(added),
                       "relation_ids": [item["plan_id"] for item in added],
                       "links": compiled_links, "column_links": column_links,
                       "concepts": registry["concepts"],
                       "record_alignments": registry["record_alignments"],
                       "class_types": dict(class_tables),
                       "type_relations": len(type_relations), "assertions": assertions,
                       "registry": registry["coverage"], "errors": []}


def _class_type(plan, table, label, evidence_id):
    """Rows that a type value points to are instances of a class named by that value."""
    type_id = f"type:class:{table}"
    if not any(item.id == type_id for item in plan.object_types):
        plan.object_types.append(DerivedType(
            id=type_id, parent="GeneralObject", label=label,
            definition=f"{label} 记录；类名取自引用方的类型取值",
            evidence_ids=[evidence_id], category="business_type"))
    return type_id


_TYPE_PREDICATES = {
    ("Metric", "Measure"): ("depends_on", "measure_binding"),
    ("GeneralObject", "Dimension"): ("related_to", "resource_scope_binding"),
    ("GeneralObject", "Measure"): ("related_to", "resource_measure_binding"),
    ("GeneralObject", "GeneralObject"): ("related_to", "resource_object_binding"),
}


def _type_relations(data, plan, links, max_rows=20000):
    """Business relations between typed rows that a structural link connects.

    A row is typed when its table is a registry or a class table. A table
    whose business key is a reference to a registry key extends that
    registry, so its other references belong to the registry row.
    """
    from .relation_contract import (canonical_relation_definition, canonical_relation_id,
                                    canonical_relation_label)
    types = {item.id: item for item in plan.object_types if item.category == "business_type"}

    def root(type_id):
        seen = set()
        while type_id in types and type_id not in seen:
            seen.add(type_id)
            type_id = types[type_id].parent
        return type_id

    row_types = dict(getattr(data, "_registry_row_types", {}) or {})
    class_tables = dict(getattr(data, "_class_tables", {}) or {})
    extensions = _extensions(data, links, row_types)
    relations, assertions = {}, {}
    object_ids = getattr(data, "_registry_record_to_object", {}) or {}
    reference_scopes = getattr(data, "_registry_reference_scopes", {}) or {}
    for link in links:
        source, target = link["source_table"], link["target_table"]
        if link["predicate_name"] == "member_namespace" or extensions.get(source, {}).get("plan_id") == link["plan_id"]:
            continue
        if source not in class_tables and source not in extensions and not _typed_table(data, source):
            continue
        target_index = _row_index(data, target, link["target_column"], link["scope_bindings"], max_rows)
        base_index = None
        if source in extensions:
            base = extensions[source]
            base_index = _row_index(data, base["target_table"], base["target_column"],
                                    base["scope_bindings"], max_rows)
        for row in _source_rows(data, source, link, max_rows):
            target_id = target_index.get(_lookup_key(row, link["source_column"], link["scope_bindings"]))
            target_type = class_tables.get(target) or row_types.get(target_id)
            if target_id is None or target_type not in types:
                continue
            if base_index is not None:
                base = extensions[source]
                subject = base_index.get(_lookup_key(row, base["source_column"], base["scope_bindings"]))
                source_type = row_types.get(subject)
            else:
                subject = data.record_id(source, row)
                source_type = class_tables.get(source) or row_types.get(subject)
            if source_type not in types or source_type == target_type:
                continue
            parent, name = _TYPE_PREDICATES.get((root(source_type), root(target_type)), ("points_to", None))
            relation_id = canonical_relation_id(parent, source_type, target_type, predicate_name=name)
            evidence = [link["structural_evidence_id"]]
            if relation_id not in relations:
                relations[relation_id] = DerivedType(
                    id=relation_id, parent=parent, label=canonical_relation_label(parent, name),
                    predicate_name=name, definition=canonical_relation_definition(parent, name),
                    evidence_ids=evidence, category="business_relation_type",
                    domain=[source_type], range=[target_type],
                    endpoint_basis="structural_coverage", evidence_scope="source_schema")
            else:
                relations[relation_id].evidence_ids = sorted(set(relations[relation_id].evidence_ids) | set(evidence))
            if base_index is not None:
                # The extension row itself is not the subject; the registry row it
                # extends is, so this edge is new rather than a copy of a plan edge.
                local_subject, local_target = subject, target_id
                subject, target_id = object_ids.get(subject, subject), object_ids.get(target_id, target_id)
                edge_id = "structural-relation:" + digest([subject, target_id, relation_id])[:24]
                via_record = data.record_id(source, row)
                witness = {"source_record_id": local_subject, "target_record_id": local_target,
                           "via_record_id": via_record,
                           "scope": {field: row.get(field) for field in sorted(link["scope_bindings"])},
                           "source_reference_scope": reference_scopes.get(local_subject, {}),
                           "target_reference_scope": reference_scopes.get(local_target, {})}
                fields = {link["source_column"], base["source_column"], *link["scope_bindings"],
                          *base["scope_bindings"]}
                for field in sorted(fields):
                    eid = record_evidence_id(via_record, field)
                    data.evidence[eid] = {"id": eid, "origin": "observed_record", "raw_fragment": row.get(field),
                        "source_ref": {"table": source, "record_id": via_record, "row": row["__r2_row"],
                                       "column": field, "snapshot_id": data.snapshot_id}}
                    evidence.append(eid)
                if edge_id in assertions:
                    known = assertions[edge_id]
                    known["evidence_ids"] = sorted(set(known["evidence_ids"]) | set(evidence))
                    if witness not in known["reference_scopes"]:
                        known["reference_scopes"].append(witness)
                    continue
                assertions[edge_id] = {
                    "id": edge_id, "subject": subject, "predicate": relation_id, "object": target_id,
                    "parent_relation": parent, "predicate_name": name or parent,
                    "via_record_id": via_record, "plan_id": link["plan_id"],
                    "reference_scopes": [witness],
                    "evidence_ids": evidence,
                    "decision": {"status": "accepted", "method": "structural_type_relation"}}
    return list(relations.values()), list(assertions.values())


def _typed_table(data, table):
    return bool((data.tables.get(table) or {}).get("structural_rows_compiled"))


def _extensions(data, links, row_types):
    """A table keyed by a registry's code extends that registry row by row.

    Exactly one key column of the table may reference a registry; with two,
    which registry the rows belong to is not decided by structure.
    """
    classes = getattr(data, "_class_tables", None) or {}
    candidates = {}
    for link in links:
        source = link["source_table"]
        info = data.tables.get(source) or {}
        if (info.get("structural_rows_compiled") or info.get("member_table") or link.get("selector")
                or source in classes):
            continue
        if not _typed_table(data, link["target_table"]):
            continue
        # The referencing column must be the table's own business key, not
        # merely a column that happens to be unique in this snapshot.
        key = (info.get("table_keys") or {}).get("business_key") or {}
        if key.get("columns") == [link["source_column"]]:
            candidates.setdefault(source, []).append(link)
    return {source: items[0] for source, items in candidates.items() if len(items) == 1}


def _lookup_key(row, column, scope_bindings):
    key = (_text(row.get(column)), *(_text(row.get(local)) for local in sorted(scope_bindings)))
    return key if all(key) else None


def _row_index(data, table, column, scope_bindings, limit):
    index, repeated = {}, set()
    target_columns = [scope_bindings[local] for local in sorted(scope_bindings)]
    for row in _rows(data, table, limit):
        key = (_text(row.get(column)), *(_text(row.get(item)) for item in target_columns))
        if not all(key):
            continue
        if key in index:
            repeated.add(key)
        index[key] = data.record_id(table, row)
    for key in repeated:
        index.pop(key, None)
    return index


def _source_rows(data, table, link, limit):
    selector = link.get("selector")
    for row in _rows(data, table, limit):
        if selector and row.get(selector["field"]) not in selector["values"]:
            continue
        yield row


def _rows(data, table, limit):
    cursor = data.db.execute(
        f"SELECT * FROM {qi(data.tables[table]['sql_name'])} ORDER BY __r2_row LIMIT ?", [limit])
    names = [column[0] for column in cursor.description]
    for values in cursor.fetchall():
        yield dict(zip(names, values))


def _text(value):
    return "" if value is None else str(value).strip()


def materialize_class_rows(data, plan, existing_ids=(), *, max_records=None):
    """Instance nodes for rows of class tables, keyed by the row's record id."""
    types = {item.id for item in plan.object_types}
    existing = set(existing_ids)
    objects, pending = [], []
    for table, type_id in sorted((getattr(data, "_class_tables", {}) or {}).items()):
        if type_id not in types:
            continue
        info = data.tables[table]
        label_column = _label_column(data, info)
        for row in _rows(data, table, max_records if max_records is not None else 1200000):
            record_id = data.record_id(table, row)
            if record_id in existing:
                continue
            if max_records is not None and len(objects) >= max_records:
                pending.append({"id": record_id, "reason": "class_row_record_limit"})
                continue
            existing.add(record_id)
            objects.append({
                "id": record_id, "type": type_id, "ontology_type_id": type_id,
                "label": _text(row.get(label_column)) if label_column else record_id,
                "instance_kind": "class_row_instance",
                "source_ref": {"table": table, "row": row["__r2_row"], "record_id": record_id,
                               "snapshot_id": data.snapshot_id},
                "evidence_ids": [item for item in next(t for t in plan.object_types if t.id == type_id).evidence_ids
                                 if item in data.evidence],
                "business_observation_claim": False})
    return {"objects": objects, "pending": pending}


def _label_column(data, info):
    names = [column for column in info.get("column_names") or ()
             if re.search(r"(?:^|_)(?:name|title)$|名称", column, re.I)]
    return names[0] if names else None


def _structural_evidence(data, relation):
    evidence_id = "structural:" + relation["plan_id"]
    data.evidence[evidence_id] = {
        "id": evidence_id, "origin": "structural_key_reference",
        "raw_fragment": json.dumps(relation["check"], ensure_ascii=False, sort_keys=True),
        "source_ref": {"table": relation["source_table"], "column": relation["source_column"],
                       "target_table": relation["target_table"],
                       "target_column": relation["target_column"],
                       "snapshot_id": getattr(data, "snapshot_id", None)}}
    return evidence_id


def _ensure_schema_evidence(data):
    evidence = getattr(data, "evidence", None)
    if evidence is None:
        return
    for name, info in data.tables.items():
        table_id = "schema:" + name
        evidence.setdefault(table_id, {
            "id": table_id, "origin": "declared_metadata",
            "raw_fragment": info.get("table_comment") or name,
            "source_ref": {"table": name}})
        for column in info.get("column_names") or []:
            column_id = f"schema:{name}:{column}"
            evidence.setdefault(column_id, {
                "id": column_id, "origin": "declared_metadata", "raw_fragment": column,
                "source_ref": {"table": name, "column": column}})


def _relations(data):
    found = []
    tables = getattr(data, "tables", {})
    for name, info in sorted(tables.items()):
        member = info.get("member_table") or {}
        dimension_name = member.get("dimension_table")
        dimension = tables.get(dimension_name) or {}
        registry = dimension.get("dimension_registry") or {}
        if member and registry:
            link = _checked_link(data, name, info, member["namespace_column"], dimension_name,
                                 dimension, registry, "member_namespace",
                                 "成员表的维度编码指向维度定义")
            if link:
                found.append(link)
    for name, info in sorted(tables.items()):
        for key, predicate, definition in (
                ("quantity_registry", "quantity_code", "编码值指向数量登记表"),
                ("dimension_registry", "dimension_code", "编码值指向维度定义")):
            registry = info.get(key)
            if not registry:
                continue
            for other, other_info in sorted(tables.items()):
                if other == name or other_info.get("member_table"):
                    continue
                for column in other_info.get("column_names") or []:
                    if not _same_code(column, registry["code_column"]):
                        continue
                    link = _checked_link(data, other, other_info, column, name, info, registry,
                                         predicate, definition)
                    if link:
                        found.append(link)
    found.extend(_typed_references(data))
    return found


def _checked_link(data, source_table, source_info, source_column, target_table, target_info,
                  registry, predicate, definition):
    scope = [column for column in registry.get("scope_columns") or []
             if column in (source_info.get("column_names") or ())]
    if len(scope) != len(registry.get("scope_columns") or []):
        return None
    check = _scoped_check(data, source_info, source_column, target_info,
                          registry["code_column"], scope)
    if check is None or check["eligible"] == 0 or check["ambiguous"]:
        return None
    if check["unique"] < 0.95 * check["eligible"]:
        return None
    link = _link(source_table, source_column, target_table, registry["code_column"],
                 "points_to", predicate, definition)
    link["scope_bindings"] = {column: column for column in scope}
    link["check"] = check
    return link


def _scoped_check(data, source, source_column, target, target_column, scope, selector=None):
    if not hasattr(data, "db"):
        return None
    left, right = qi(source_column), qi(target_column)
    keys = ", ".join([f"t.{right} AS ref", *[f"t.{qi(column)} AS s_{index}"
                                            for index, column in enumerate(scope)]])
    group = ", ".join(["ref", *[f"s_{index}" for index in range(len(scope))]])
    join = " AND ".join([f"s.{left} = k.ref",
                         *[f"s.{qi(column)} = k.s_{index}" for index, column in enumerate(scope)]])
    where, params = "", []
    if selector:
        field, values = selector
        where = f" AND s.{qi(field)} IN ({', '.join('?' for _ in values)})"
        params = list(values)
    eligible, unique, ambiguous, missing = data.db.execute(
        f"WITH k AS (SELECT {group}, count(*) AS n FROM (SELECT {keys} FROM "
        f"{qi(target['sql_name'])} t WHERE t.{right} IS NOT NULL) GROUP BY {group}) "
        f"SELECT count(*), count(*) FILTER (WHERE k.n = 1), count(*) FILTER (WHERE k.n > 1), "
        f"count(*) FILTER (WHERE k.n IS NULL) FROM {qi(source['sql_name'])} s "
        f"LEFT JOIN k ON {join} WHERE s.{left} IS NOT NULL AND "
        f"trim(CAST(s.{left} AS VARCHAR)) <> ''{where}", params).fetchone()
    return {"eligible": int(eligible), "unique": int(unique), "ambiguous": int(ambiguous),
            "missing": int(missing), "scope": list(scope),
            "selector": {selector[0]: list(selector[1])} if selector else {}}


# Generic business words for type values written in Chinese. They name the
# same thing as the English token; no table or column of a dataset is listed.
_TYPE_WORDS = (("接口", "api"), ("服务", "api"), ("卡片", "card"), ("看板", "card"),
               ("指标", "metric"), ("度量", "measure"))
_REGISTRY_CUES = {"metric": "Metric", "indicator": "Metric", "measure": "Measure"}


def _cue(literal):
    latin = re.match(r"[A-Za-z]+", literal.strip())
    if latin:
        return latin.group().casefold()
    return next((cue for word, cue in _TYPE_WORDS if word in literal), None)


def _typed_references(data, max_values=32):
    """``X_type`` beside ``X_id`` or ``X_field``: a type value says where the reference points.

    A value names a table when its word equals the last name token of exactly
    one table, or names a quantity registry by its role. A value can also be
    a code of a registry itself. Every link must resolve to one row of the
    target key inside the scope.
    """
    tables = getattr(data, "tables", {})
    by_token = {}
    for name, info in tables.items():
        tokens = [item for item in re.split(r"[^a-z0-9]+", str(
            info.get("table_name") or name.rsplit(".", 1)[-1]).casefold()) if item]
        if tokens:
            by_token.setdefault(tokens[-1], []).append(name)
    registries = {}
    for name, info in tables.items():
        role = (info.get("quantity_registry") or {}).get("role")
        if role:
            registries.setdefault(role, []).append(name)
    found = []
    for name, info in sorted(tables.items()):
        columns = info.get("column_names") or []
        for type_column in columns:
            if not type_column.endswith("_type"):
                continue
            prefix = type_column[:-len("_type")]
            references = [column for column in (prefix + "_id", prefix + "_field") if column in columns]
            field = qi(type_column)
            literals = [str(value) for (value,) in data.db.execute(
                f"SELECT DISTINCT {field} FROM {qi(info['sql_name'])} WHERE {field} IS NOT NULL "
                f"AND trim(CAST({field} AS VARCHAR)) <> '' ORDER BY 1 LIMIT ?", [max_values + 1]).fetchall()]
            if len(literals) > max_values:
                continue
            found.extend(_code_valued_types(data, name, info, type_column, literals))
            if not references:
                continue
            groups = {}
            for literal in literals:
                cue = _cue(literal)
                if cue:
                    groups.setdefault(cue, []).append(literal)
            for cue, values in sorted(groups.items()):
                targets = [table for table in by_token.get(cue, []) if table != name]
                role_targets = registries.get(_REGISTRY_CUES.get(cue), [])
                if len(targets) != 1 and len(role_targets) == 1:
                    targets = role_targets
                if len(targets) != 1:
                    continue
                for reference in references:
                    link = _typed_link(data, name, info, type_column, reference, values, targets[0])
                    if link:
                        found.append(link)
                        break
    found.extend(_json_code_links(data, found))
    found.extend(_co_owned_references(data, found))
    return found


def _co_owned_references(data, links, max_values=8):
    """Rows owned by the same resource reference each other inside that owner.

    Two tables whose ``X_id`` columns point at the same owner tables share an
    owner. A reference from one to the other's key is checked with the owner
    id bound as well, and a low-cardinality column of the source decides, value
    by value, which target table the reference resolves to.
    """
    owners = {}
    for link in links:
        if link["predicate_name"] == "typed_reference":
            owners.setdefault(link["source_table"], {}).setdefault(
                link["source_column"], set()).add(link["target_table"])
    linked = {(link["source_table"], link["source_column"]) for link in links}
    found = []
    for source, source_owner in sorted(owners.items()):
        source_info = data.tables[source]
        source_keys = {column for key in ((source_info.get("table_keys") or {}).get("keys") or ())
                       for column in key["columns"]}
        scope = list((source_info.get("table_keys") or {}).get("scope_columns") or [])
        for column in source_info.get("column_names") or ():
            if (column in source_keys or column in scope or column in source_owner
                    or (source, column) in linked or column.endswith("_type")):
                continue
            options = []
            for target, target_owner in sorted(owners.items()):
                if target == source:
                    continue
                shared = sorted(item for item in source_owner
                                if item in target_owner and source_owner[item] & target_owner[item])
                if not shared:
                    continue
                target_info = data.tables[target]
                for key in ((target_info.get("table_keys") or {}).get("keys") or ()):
                    if len(key["columns"]) != 1 or not set(key["scope"]) <= set(scope):
                        continue
                    options.append((target, key["columns"][0], key["scope"], shared))
            if len(options) < 2:
                continue
            for selector_column in source_info.get("column_names") or ():
                if selector_column in (column, *scope, *source_owner) or selector_column.endswith("_type"):
                    continue
                choice = _split_by_values(data, source_info, column, selector_column, options, max_values)
                for target, key, key_scope, shared, values, check in choice:
                    bindings = {item: item for item in [*key_scope, *shared]}
                    item = _link(source, column, target, key, "points_to", "co_owned_reference",
                                 f"{selector_column} 取值 {values[0]} 时，{column} 指向同一所属资源下的该表记录")
                    item["plan_id"] += f"[{selector_column}={values[0]}]"
                    item["scope_bindings"] = bindings
                    item["selector"] = {"field": selector_column, "values": values}
                    item["check"] = check
                    found.append(item)
                if choice:
                    break
    return found


def _value_names_table(value, table):
    word = value.strip().casefold()
    tokens = [item for item in re.split(r"[^a-z0-9]+", table.rsplit(".", 1)[-1].casefold()) if item]
    return len(word) >= 2 and sum(token.startswith(word) for token in tokens) == 1


def _split_by_values(data, source_info, column, selector_column, options, max_values):
    field = qi(selector_column)
    values = [str(value) for (value,) in data.db.execute(
        f"SELECT DISTINCT {field} FROM {qi(source_info['sql_name'])} WHERE {field} IS NOT NULL "
        f"AND trim(CAST({field} AS VARCHAR)) <> '' ORDER BY 1 LIMIT ?", [max_values + 1]).fetchall()]
    if not 2 <= len(values) <= max_values:
        return []
    chosen = []
    for value in values:
        resolved = []
        for target, key, key_scope, shared in options:
            check = _scoped_check(data, source_info, column, data.tables[target], key,
                                  [*key_scope, *shared], selector=(selector_column, [value]))
            if check is None or not check["eligible"]:
                continue
            resolved.append((check["unique"] / check["eligible"], target, key, key_scope, shared, check))
        good = [item for item in resolved if item[0] >= 0.95 and not item[5]["ambiguous"]]
        if len(good) > 1:
            # Parallel numbering can resolve one value in several tables. The
            # value then has to name its table, as IN names an input table.
            good = [item for item in good if _value_names_table(value, item[1])]
            if len(good) != 1:
                return []
        elif len(good) == 1:
            if (any(item[0] >= 0.5 for item in resolved if item is not good[0])
                    and not _value_names_table(value, good[0][1])):
                return []
        else:
            return []
        chosen.append((good[0][1], good[0][2], good[0][3], good[0][4], [value], good[0][5]))
    if len({item[0] for item in chosen}) < 2:
        return []
    return chosen


def _accepted(check, numeric):
    """Integers coincide across tables, so a numeric key needs near-total resolution."""
    if check is None or not check["eligible"] or check["ambiguous"]:
        return False
    return check["unique"] >= (0.95 if numeric else 0.8) * check["eligible"]


def _json_code_links(data, links, sample=200):
    """A JSON list of codes in a typed row points where the row's type points."""
    from .discovery import _source_from, json_scalar_paths
    found = []
    for link in links:
        if link["predicate_name"] not in ("typed_reference", "type_value_code") or not link.get("selector"):
            continue
        source, target = link["source_table"], link["target_table"]
        source_info, target_info = data.tables[source], data.tables[target]
        if link["predicate_name"] == "type_value_code":
            # The type value is a dimension code; its members carry the list values.
            member_table = next((name for name, info in sorted(data.tables.items())
                                 if (info.get("member_table") or {}).get("dimension_table") == target), None)
            if member_table is None:
                continue
            spec = data.tables[member_table]["member_table"]
            target, target_info = member_table, data.tables[member_table]
            code = spec["code_column"]
            bindings = {column: column for column in spec.get("scope_columns") or []}
            bindings[link["source_column"]] = spec["namespace_column"]
        else:
            registry = target_info.get("quantity_registry") or target_info.get("dimension_registry")
            if not registry:
                continue
            code = registry["code_column"]
            bindings = dict(link["scope_bindings"])
        if not set(bindings) <= set(source_info.get("column_names") or ()):
            continue
        selector = link["selector"]
        skip = {selector["field"], *bindings} - {link["source_column"]}
        for column in source_info.get("column_names") or ():
            if column in skip or (link["predicate_name"] == "type_value_code" and column == link["source_column"]):
                continue
            values = [value for (value,) in data.db.execute(
                f"SELECT {qi(column)} FROM {qi(source_info['sql_name'])} "
                f"WHERE {qi(selector['field'])} IN ({', '.join('?' for _ in selector['values'])}) "
                f"AND (trim(CAST({qi(column)} AS VARCHAR)) LIKE '[%' "
                f"OR trim(CAST({qi(column)} AS VARCHAR)) LIKE '{{%') LIMIT ?",
                [*selector["values"], sample]).fetchall()]
            # The reference column itself may also hold an object such as
            # {"code": ...}; other columns contribute only lists of codes.
            own = column == link["source_column"]
            paths = sorted({tuple(path) for value in values for path, _ in json_scalar_paths(value)
                            if own or "[]" in path})
            for path in paths:
                check = _json_check(data, source_info, column, list(path), target_info, code,
                                    bindings, selector, _source_from)
                if not _accepted(check, numeric=False):
                    continue
                shown = "/".join(path)
                item = _link(source, column, target, code, "points_to", "json_code_list",
                             f"{column} 的 JSON 编码列表随 {selector['field']} 指向该表记录")
                item["plan_id"] = (f"plan:{source}.{column}/{shown}-{target}.{code}"
                                   f"[{selector['field']}={selector['values'][0]}]")
                item["source_path"] = list(path)
                item["scope_bindings"] = bindings
                item["selector"] = dict(selector)
                item["check"] = check
                found.append(item)
    return found


def _json_check(data, source, column, path, target, code, bindings, selector, source_from):
    value, origin = source_from(qi(source["sql_name"]), qi(column), path)
    local = sorted(bindings)
    keys = ", ".join([f"t.{qi(code)} AS ref", *[f"t.{qi(bindings[item])} AS s_{index}"
                                               for index, item in enumerate(local)]])
    group = ", ".join(["ref", *[f"s_{index}" for index in range(len(local))]])
    picked = ", ".join([f"{value} AS v", *[f"s.{qi(item)} AS c_{index}" for index, item in enumerate(local)]])
    join = " AND ".join(["x.v = k.ref", *[f"x.c_{index} = k.s_{index}" for index in range(len(local))]])
    marks = ", ".join("?" for _ in selector["values"])
    try:
        eligible, unique, ambiguous, missing = data.db.execute(
            f"WITH k AS (SELECT {group}, count(*) AS n FROM (SELECT {keys} FROM "
            f"{qi(target['sql_name'])} t WHERE t.{qi(code)} IS NOT NULL) GROUP BY {group}), "
            f"x AS (SELECT {picked} {origin} WHERE s.{qi(selector['field'])} IN ({marks})) "
            f"SELECT count(*), count(*) FILTER (WHERE k.n = 1), count(*) FILTER (WHERE k.n > 1), "
            f"count(*) FILTER (WHERE k.n IS NULL) FROM x LEFT JOIN k ON {join} "
            f"WHERE x.v IS NOT NULL AND trim(x.v) <> ''", list(selector["values"])).fetchone()
    except Exception:
        return None
    return {"eligible": int(eligible), "unique": int(unique), "ambiguous": int(ambiguous),
            "missing": int(missing), "scope": local, "source_path": path,
            "selector": {selector["field"]: list(selector["values"])}}


def _code_valued_types(data, source_table, source_info, type_column, literals):
    """Type values that are themselves codes of one registry point at that registry row."""
    found = []
    for target_table, target_info in sorted(data.tables.items()):
        registry = target_info.get("dimension_registry") or target_info.get("quantity_registry")
        if not registry or target_table == source_table:
            continue
        code = registry["code_column"]
        codes = {str(value) for (value,) in data.db.execute(
            f"SELECT DISTINCT {qi(code)} FROM {qi(target_info['sql_name'])} "
            f"WHERE {qi(code)} IS NOT NULL").fetchall()}
        matched = sorted(set(literals) & codes)
        if not matched:
            continue
        scope = list(registry.get("scope_columns") or [])
        if not set(scope) <= set(source_info.get("column_names") or ()):
            continue
        check = _scoped_check(data, source_info, type_column, target_info, code, scope,
                              selector=(type_column, matched))
        if not _accepted(check, numeric=False):
            continue
        link = _link(source_table, type_column, target_table, code, "points_to",
                     "type_value_code", f"{type_column} 的取值本身是登记表编码")
        link["scope_bindings"] = {column: column for column in scope}
        link["selector"] = {"field": type_column, "values": matched}
        link["check"] = check
        found.append(link)
    return found


def _typed_link(data, source_table, source_info, type_column, id_column, values, target_table):
    target_info = data.tables[target_table]
    keys = [key for key in ((target_info.get("table_keys") or {}).get("keys") or ())
            if len(key["columns"]) == 1]
    registry = target_info.get("quantity_registry") or target_info.get("dimension_registry")
    if registry:
        keys = [{"columns": [registry["code_column"]], "scope": list(registry.get("scope_columns") or []),
                 "kind": "business"}]
    for key in keys:
        scope = list(key["scope"])
        if not set(scope) <= set(source_info.get("column_names") or ()):
            continue
        check = _scoped_check(data, source_info, id_column, target_info, key["columns"][0], scope,
                              selector=(type_column, values))
        if not _accepted(check, numeric=key["kind"] != "business"):
            continue
        label = next((value for value in values if value.isupper()), values[0]).strip()
        link = _link(source_table, id_column, target_table, key["columns"][0], "points_to",
                     "typed_reference", f"{type_column} 取值 {label} 时，{id_column} 指向该表记录")
        link["plan_id"] += f"[{type_column}={label}]"
        link["scope_bindings"] = {column: column for column in scope}
        link["selector"] = {"field": type_column, "values": list(values)}
        link["class_label"] = label
        link["check"] = check
        return link
    return None


def _same_code(source, target):
    def token(name):
        return re.sub(r"[^a-z0-9]+", "", name.casefold())
    left, right = token(source), token(target)
    return bool(left and right and (left == right or left.endswith(right) or right.endswith(left)))


def _column_links(data):
    """Code, name, and parent columns that belong to one structural table."""
    found = []
    seen = set()
    for name, info in sorted(getattr(data, "tables", {}).items()):
        member = info.get("member_table") or {}
        if member:
            code = member.get("code_column")
            for column in member.get("name_columns") or []:
                _remember_column(found, seen, name, code, column, "code_name")
            if member.get("parent_column"):
                _remember_column(found, seen, name, code, member["parent_column"], "parent_code")
        for key in ("quantity_registry", "dimension_registry"):
            registry = info.get(key) or {}
            code, label = registry.get("code_column"), registry.get("name_column")
            if code and label and code != label:
                _remember_column(found, seen, name, code, label, "code_name")
    return found


def _remember_column(found, seen, table, code, other, kind):
    key = (table, code, other, kind)
    if not code or not other or code == other or key in seen:
        return
    seen.add(key)
    found.append({"table": table, "code_column": code, "other_column": other, "kind": kind})


def _link(source_table, source_column, target_table, target_column, parent, name, definition):
    evidence = [f"schema:{source_table}:{source_column}", f"schema:{target_table}:{target_column}"]
    return {"plan_id": f"plan:{source_table}.{source_column}-{target_table}.{target_column}",
            "source_table": source_table, "source_column": source_column,
            "target_table": target_table, "target_column": target_column,
            "parent": parent, "predicate_name": name, "definition": definition,
            "evidence_ids": evidence, "scope_bindings": {}}
