"""Dimension members and their aliases stay instances.

A member table has a coarse namespace code, a member code and a name.
An alias table holds surface words that point at those codes. Neither is a
business type. Detection uses column shape and full-table counts, not a
dataset mapping.
"""

from __future__ import annotations

import json
import re

from .storage import qi


_CODE = re.compile(r"(?:^|_)(?:code|id)$|编码|代码", re.I)
_NAME = re.compile(r"(?:^|_)(?:name|title|label|cn_name|en_name)$|名称|名字|标题", re.I)
_NAMESPACE = re.compile(
    r"(?:^|_)(?:dim|dimension)_(?:code|id)$|维度编码", re.I)
_MEMBER_CODE = re.compile(
    r"(?:^|_)(?:member|item)_(?:code|id)$|成员编码", re.I)
_LEVEL = re.compile(r"(?:^|_)level(?:_no|_num)?$|层级", re.I)
_PARENT = re.compile(r"parent_.*(?:code|id)|上级(?:编码|代码)", re.I)
_ALIAS = re.compile(r"(?:^|_)(?:word|synonym|alias|lexeme|term)$|同义词|词条|别名", re.I)
_ROW_ID = re.compile(r"(?:^|_)id$", re.I)
_DEFINITION = re.compile(r"definition|description|formula|说明|定义|公式", re.I)


def annotate_dimension_members(data):
    """Mark member, dimension and alias tables. Return a short report."""
    report = {"member_tables": [], "dimension_tables": [], "alias_tables": []}
    if not hasattr(data, "db"):
        return report
    for name, info in sorted(getattr(data, "tables", {}).items()):
        if info.get("quantity_registry"):
            continue
        try:
            member = _member_table(data, info)
        except Exception as exc:
            report["member_tables"].append({"table": name, "reason": "query_failed",
                                            "error_type": type(exc).__name__})
            continue
        if member is None:
            continue
        info["member_table"] = member
        report["member_tables"].append({"table": name, **member})
    for name, info in sorted(data.tables.items()):
        if info.get("quantity_registry") or info.get("member_table"):
            continue
        try:
            dimension = _dimension_table(data, info, report["member_tables"])
        except Exception as exc:
            report["dimension_tables"].append({"table": name, "reason": "query_failed",
                                              "error_type": type(exc).__name__})
            continue
        if dimension is None:
            continue
        info["dimension_registry"] = dimension
        report["dimension_tables"].append({"table": name, **dimension})
    for name, info in sorted(data.tables.items()):
        if info.get("quantity_registry") or info.get("member_table") or info.get("dimension_registry"):
            continue
        columns = _names(info)
        alias = next((column for column in columns if _ALIAS.search(column)), None)
        if alias is None or any(_DEFINITION.search(column) for column in columns):
            continue
        if any(column.casefold() == "namespace" for column in columns):
            continue
        if any(_CODE.search(column) and not re.search(r"(?:^|_)id$", column, re.I) for column in columns):
            continue
        info["alias_table"] = {"word_column": alias, "reason": "surface_words_without_definition"}
        report["alias_tables"].append({"table": name, "word_column": alias})
    _assign_dimension_tables(data)
    _drop_unconfirmed_dimension_registries(data, report)
    return report


def materialize_structural_members(data, plan, members, *, max_records=None, max_assertions=None):
    """Put recognized member rows on the instance graph under their structural type."""
    if max_records is not None and (type(max_records) is not int or max_records < 0):
        raise ValueError("max_records must be a nonnegative integer")
    if max_assertions is not None and (type(max_assertions) is not int or max_assertions < 0):
        raise ValueError("max_assertions must be a nonnegative integer")
    types = {item.id: item for item in getattr(plan, "object_types", [])
             if getattr(item, "category", None) == "business_type"}
    relation_ids = {item.id for item in getattr(plan, "relation_types", [])}
    rows = members.get("members", []) if isinstance(members, dict) else []
    dimension_types = getattr(data, "_dimension_row_types", {}) or {}

    def scope_key(item):
        return tuple(sorted((item.get("scope") or {}).items()))

    def node_id(item):
        # The source row's record id, so relation endpoints for this row
        # land on the same node instead of a second one.
        return item.get("record_id") or item["id"]

    groups = {}
    for item in rows:
        groups.setdefault((item.get("table"), scope_key(item), item.get("dimension_code"),
                           item.get("member_code")), []).append(node_id(item))
    objects, assertions, pending = [], [], []
    evidence = getattr(data, "evidence", {})
    for item in rows:
        if max_records is not None and len(objects) >= max_records:
            pending.append({"id": node_id(item), "reason": "structural_member_record_limit"})
            continue
        type_id = dimension_types.get((item.get("dimension_table"), scope_key(item), item.get("dimension_code")))
        if type_id not in types:
            pending.append({"id": node_id(item), "reason": "member_dimension_row_unresolved"})
            continue
        proofs = [eid for eid in types[type_id].evidence_ids if eid in evidence]
        objects.append({
            "id": node_id(item), "member_key": item["id"], "label": item.get("label"), "type": type_id,
            "ontology_type_id": type_id, "instance_kind": "dimension_member",
            "table": item["table"], "row_number": item.get("row_number"),
            "source_ref": {"table": item["table"], "row": item.get("row_number"),
                           "record_id": node_id(item), "snapshot_id": getattr(data, "snapshot_id", None)},
            "dimension_code": item.get("dimension_code"), "member_code": item.get("member_code"),
            "semantic_role": "observed_dimension_member_not_dimension_definition",
            "evidence_ids": proofs,
        })
        parent_code = item.get("parent_code")
        if not parent_code:
            continue
        matches = [parent for parent in groups.get(
            (item.get("table"), scope_key(item), item.get("dimension_code"), parent_code), [])
                   if parent != node_id(item)]
        if len(matches) != 1:
            if matches:
                pending.append({"id": node_id(item), "reason": "ambiguous_member_parent"})
            continue
        if max_assertions is not None and len(assertions) >= max_assertions:
            pending.append({"id": node_id(item), "reason": "structural_member_assertion_limit"})
            continue
        from .models import DerivedType
        from .relation_contract import canonical_relation_definition, canonical_relation_id
        relation_id = canonical_relation_id("contains", type_id, type_id)
        if relation_id not in relation_ids:
            if not proofs:
                pending.append({"id": item["id"], "reason": "member_parent_relation_type_absent"})
                continue
            plan.relation_types.append(DerivedType(
                id=relation_id, parent="contains", label="contains", predicate_name="contains",
                definition=canonical_relation_definition("contains"), evidence_ids=proofs,
                category="business_relation_type", domain=[type_id], range=[type_id],
                endpoint_basis="structural_coverage", evidence_scope="source_schema"))
            relation_ids.add(relation_id)
        assertions.append({
            "id": "member-parent:" + node_id(item), "subject": matches[0],
            "predicate": relation_id, "parent_relation": "contains", "predicate_name": "contains",
            "object": node_id(item), "semantics": "reference",
            "definition": canonical_relation_definition("contains"),
            "evidence_ids": proofs,
            "decision": {"status": "accepted", "method": "structural_member_parent"},
        })
    emitted = {item["id"] for item in objects}
    kept = []
    for edge in assertions:
        if edge["subject"] in emitted and edge["object"] in emitted:
            kept.append(edge)
        else:
            pending.append({"id": edge["object"], "reason": "member_parent_not_materialized"})
    return {"objects": objects, "assertions": kept, "pending": pending}


def compile_dimension_members(data, *, max_rows=100000):
    """Read member rows and attach alias words. Does not create types."""
    if type(max_rows) is not int or max_rows < 1:
        raise ValueError("max_rows must be a positive integer")
    members = []
    omitted = 0
    by_code = {}
    for name, info in sorted(getattr(data, "tables", {}).items()):
        spec = info.get("member_table")
        if not spec:
            continue
        scope = list(spec.get("scope_columns") or [])
        columns = [*scope, spec["namespace_column"], spec["code_column"], *spec["name_columns"]]
        for optional in ("parent_column", "level_column"):
            if spec.get(optional) and spec[optional] not in columns:
                columns.append(spec[optional])
        columns.extend(column for column in info.get("pk") or () if column not in columns)
        table = qi(info["sql_name"])
        listed = ", ".join(qi(column) for column in columns)
        rows = data.db.execute(
            f"SELECT __r2_row, {listed} FROM {table} ORDER BY __r2_row LIMIT ?",
            [max_rows + 1]).fetchall()
        if len(rows) > max_rows:
            omitted += len(rows) - max_rows
            rows = rows[:max_rows]
        for row in rows:
            record = dict(zip(["__r2_row", *columns], row))
            code = _text(record.get(spec["code_column"]))
            namespace = _text(record.get(spec["namespace_column"]))
            if not code or not namespace:
                continue
            labels = []
            for column in spec["name_columns"]:
                value = _text(record.get(column))
                if value and value not in labels:
                    labels.append(value)
            if not labels:
                continue
            scope_values = {column: _text(record.get(column)) for column in scope}
            prefix = ":".join(scope_values[column] for column in scope)
            record_id = data.record_id(name, record)
            item = {
                "id": f"member:{name}:{prefix + ':' if prefix else ''}{namespace}:{code}",
                "record_id": record_id,
                "table": name, "row_number": record["__r2_row"],
                "scope": scope_values,
                "dimension_code": namespace, "member_code": code,
                "label": labels[0], "names": labels, "aliases": [],
                "parent_code": _text(record.get(spec.get("parent_column"))) or None,
                "level": _text(record.get(spec.get("level_column"))) or None,
                "dimension_table": spec.get("dimension_table"),
            }
            members.append(item)
            by_code.setdefault(code, []).append(item)
    alias_links = 0
    for name, info in sorted(getattr(data, "tables", {}).items()):
        spec = info.get("alias_table")
        if not spec or not by_code:
            continue
        word = spec["word_column"]
        others = [column for column in _names(info) if column != word]
        table = qi(info["sql_name"])
        selected = ", ".join(qi(column) for column in [word, *others])
        rows = data.db.execute(
            f"SELECT {selected} FROM {table} ORDER BY __r2_row LIMIT ?",
            [max_rows + 1]).fetchall()
        for row in rows[:max_rows]:
            record = dict(zip([word, *others], row))
            surface = _text(record.get(word))
            if not surface:
                continue
            codes = [value for column in others for value in _strings(record.get(column))]
            hits = [item for code in codes for item in by_code.get(code, ())
                    if all(_text(record.get(column)) == value
                           for column, value in item.get("scope", {}).items() if column in record)]
            if len({item["id"] for item in hits}) != 1:
                continue
            member = hits[0]
            if surface not in member["names"] and surface not in member["aliases"]:
                member["aliases"].append(surface)
                alias_links += 1
    return {"members": members, "coverage": {
        "member_rows": len(members), "alias_links": alias_links,
        "rows_omitted": omitted, "partial": omitted > 0,
        "member_tables": [name for name, info in data.tables.items() if info.get("member_table")],
        "alias_tables": [name for name, info in data.tables.items() if info.get("alias_table")],
    }}


def _names(info):
    names = list(info.get("column_names") or ())
    if names:
        return names
    return [item.get("column_name") for item in info.get("columns", ()) if item.get("column_name")]


def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def _strings(value):
    if value is None:
        return []
    if isinstance(value, (dict, list)):
        found = []
        items = value.values() if isinstance(value, dict) else value
        for item in items:
            found.extend(_strings(item))
        return found
    text = str(value).strip()
    if text[:1] in "[{":
        try:
            return _strings(json.loads(text))
        except json.JSONDecodeError:
            return [text]
    return [text] if text else []


def _ranked(columns, order):
    """Prefer a business code over a row id when both match the same role."""
    positions = {column: index for index, column in enumerate(order)}

    def key(column):
        folded = column.casefold()
        if _NAMESPACE.search(column):
            rank = 0
        elif folded.endswith("_code") or folded.endswith("编码") or folded.endswith("代码"):
            rank = 1
        elif _ROW_ID.search(column):
            rank = 3
        else:
            rank = 2
        return rank, positions.get(column, 0)

    return sorted(columns, key=key)


def _cardinality(data, info, column):
    field, table = qi(column), qi(info["sql_name"])
    usable, distinct = data.db.execute(
        f"SELECT count(*) FILTER (WHERE {field} IS NOT NULL AND trim(CAST({field} AS VARCHAR)) <> ''), "
        f"count(DISTINCT {field}) FILTER (WHERE {field} IS NOT NULL AND "
        f"trim(CAST({field} AS VARCHAR)) <> '') FROM {table}").fetchone()
    return int(usable or 0), int(distinct or 0)


def _member_table(data, info):
    columns = _names(info)
    namespaces = [column for column in columns if _NAMESPACE.search(column)]
    codes = [column for column in columns if _MEMBER_CODE.search(column)]
    names = [column for column in columns if _NAME.search(column)]
    if not namespaces or not codes or not names:
        return None
    namespace = _ranked(namespaces, columns)[0]
    ns_usable, ns_distinct = _cardinality(data, info, namespace)
    if ns_usable < 1 or not ns_distinct:
        return None
    chosen = None
    for code in _ranked(codes, columns):
        code_usable, code_distinct = _cardinality(data, info, code)
        if code_usable >= 2 and code_distinct > ns_distinct:
            chosen = code, code_usable, code_distinct
            break
    if chosen is None:
        return None
    code, code_usable, code_distinct = chosen
    table = qi(info["sql_name"])
    scope = _scope(info)
    group = ", ".join(qi(column) for column in [*scope, namespace, code])
    ns_sql, code_sql = qi(namespace), qi(code)
    duplicate_pairs = data.db.execute(
        f"SELECT count(*) FROM (SELECT {group} FROM {table} "
        f"WHERE {ns_sql} IS NOT NULL AND trim(CAST({ns_sql} AS VARCHAR)) <> '' "
        f"AND {code_sql} IS NOT NULL AND trim(CAST({code_sql} AS VARCHAR)) <> '' "
        f"GROUP BY {group} HAVING count(*) > 1)").fetchone()[0]
    if duplicate_pairs:
        return None
    kept = []
    for column in names:
        name_sql = qi(column)
        conflicts = data.db.execute(
            f"SELECT count(*) FROM (SELECT {group}, count(DISTINCT {name_sql}) AS n "
            f"FROM {table} WHERE {ns_sql} IS NOT NULL AND {code_sql} IS NOT NULL "
            f"AND {name_sql} IS NOT NULL AND trim(CAST({name_sql} AS VARCHAR)) <> '' "
            f"GROUP BY {group}) WHERE n > 1").fetchone()[0]
        if conflicts == 0:
            kept.append(column)
    if not kept:
        return None
    parent = next((column for column in columns if _PARENT.search(column)), None)
    level = next((column for column in columns if _LEVEL.search(column)), None)
    return {"namespace_column": namespace, "code_column": code, "name_columns": kept,
            "parent_column": parent, "level_column": level, "scope_columns": scope,
            "namespace_values": ns_distinct, "member_codes": code_distinct,
            "reason": "namespace_member_code_and_name"}


def _scope(info):
    keys = info.get("table_keys") or {}
    return list(keys.get("scope_columns") or ())


def _unique_within_scope(data, info, column):
    usable, distinct = _cardinality(data, info, column)
    if usable >= 1 and distinct == usable:
        return usable, distinct, []
    scope = _scope(info)
    if not scope or usable < 1:
        return None
    field = qi(column)
    parts = ", ".join(qi(item) for item in [*scope, column])
    scoped = data.db.execute(
        f"SELECT count(*) FROM (SELECT DISTINCT {parts} FROM {qi(info['sql_name'])} "
        f"WHERE {field} IS NOT NULL AND trim(CAST({field} AS VARCHAR)) <> '')").fetchone()[0]
    if scoped == usable:
        return usable, distinct, scope
    return None


def _same_column(left, right):
    def token(name):
        return re.sub(r"[^a-z0-9]+", "", str(name).casefold())
    first, second = token(left), token(right)
    return bool(first and second and (first == second or first.endswith(second) or second.endswith(first)))


def _assign_dimension_tables(data):
    """Value overlap is a candidate. Only one name-aligned registry becomes the owner."""
    for name, info in data.tables.items():
        member = info.get("member_table")
        if not member:
            continue
        member.pop("dimension_table", None)
        hits = []
        for dim_name, dim_info in data.tables.items():
            registry = dim_info.get("dimension_registry") or {}
            code = registry.get("code_column")
            if not code:
                continue
            covered = _contained(data, name, member["namespace_column"], dim_info, code)
            if covered is None or covered < 0.9:
                continue
            hits.append((dim_name, code, covered))
        aligned = [item for item in hits if _same_column(member["namespace_column"], item[1])
                   or _NAMESPACE.search(item[1])]
        member["dimension_candidates"] = [item[0] for item in hits]
        if len(aligned) == 1:
            member["dimension_table"] = aligned[0][0]
            member["dimension_link"] = "namespace_column_alignment"


def _drop_unconfirmed_dimension_registries(data, report):
    """Value overlap is a reference candidate, not a Dimension classification."""
    confirmed = {info["member_table"]["dimension_table"]
                 for info in data.tables.values()
                 if (info.get("member_table") or {}).get("dimension_table")}
    dropped = []
    for name, info in data.tables.items():
        registry = info.get("dimension_registry") or {}
        code = registry.get("code_column") or ""
        if not registry or name in confirmed or _NAMESPACE.search(code):
            continue
        info.pop("dimension_registry", None)
        dropped.append(name)
    report["dimension_tables"] = [item for item in report["dimension_tables"]
                                  if item["table"] not in dropped]
    report["dimension_reference_candidates"] = dropped


def _dimension_table(data, info, member_tables):
    columns = _names(info)
    codes = [column for column in columns if _CODE.search(column) or _NAMESPACE.search(column)]
    names = [column for column in columns if _NAME.search(column) and column not in codes]
    if not codes or not names or any(_MEMBER_CODE.search(column) for column in columns):
        return None
    code = None
    usable = distinct = 0
    scope = []
    for column in _ranked(codes, columns):
        found = _unique_within_scope(data, info, column)
        if found is not None:
            usable, distinct, scope = found
            code = column
            break
    if code is None:
        return None
    linked = None
    for member in member_tables:
        if member.get("reason") == "query_failed":
            continue
        covered = _contained(data, member["table"], member["namespace_column"], info, code)
        if covered is not None and covered >= 0.9:
            linked = member["table"]
            break
    named = bool(_NAMESPACE.search(code))
    if linked is None and not named:
        return None
    return {"code_column": code, "name_column": names[0], "usable_codes": usable,
            "distinct_codes": distinct, "member_table": linked, "scope_columns": scope,
            "reason": "member_namespace_values" if linked else "dimension_code_column"}


def _contained(data, member_table, namespace, dimension_info, code):
    member = data.tables[member_table]
    left, right = qi(namespace), qi(code)
    source, target = qi(member["sql_name"]), qi(dimension_info["sql_name"])
    total, matched = data.db.execute(
        f"SELECT count(DISTINCT s.{left}), count(DISTINCT s.{left}) FILTER ("
        f"WHERE EXISTS (SELECT 1 FROM {target} d WHERE d.{right} = s.{left})) "
        f"FROM {source} s WHERE s.{left} IS NOT NULL AND trim(CAST(s.{left} AS VARCHAR)) <> ''"
    ).fetchone()
    if not total:
        return None
    return matched / total
