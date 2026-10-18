"""Shared definition identity, separate from the scoped codes that reference it.

This is exact structural normalization, not fuzzy name matching. Formula
references resolve inside the source scope before their definitions are compared.
Missing descriptions can join one unambiguous definition, never bridge conflicts.
"""
from __future__ import annotations

from collections import defaultdict
import json
import re
import unicodedata

from .calculation_contracts import parse_calculation
from .storage import digest


_REFERENCE = re.compile(
    r"^(?:tenant|business|domain|source|owner|namespace|organization|org)_(?:id|code)$"
    r"|^租户|^业务(?:编码|标识)|^领域", re.I)
_DEFINITION = re.compile(r"definition|description|desc|说明|定义|口径", re.I)
_SEMANTIC = re.compile(
    r"^(?:standard_name|period|frequency|grain|caliber|calibre|filter_sql|filter_expression|"
    r"budget_flag|forecast_flag|actual_flag|target_flag|remain_flag|estimate_flag|rank_flag|"
    r"hierarchy_json|dim_level_count|primary_code_field|primary_name_field)$"
    r"|统计期间|计算口径|统计口径|粒度|过滤条件|标准名称", re.I)


def text(value):
    return "" if value is None else str(value).strip()


def normalized(value):
    # Whitespace inside SQL/string literals is data, not formatting. Unsupported
    # expressions must remain exact rather than being normalized by a text split.
    value = text(value)
    if "'" in value or '"' in value:
        return value
    return " ".join(unicodedata.normalize("NFKC", value).split())


def describe_entry(table, info, row, spec, record_id):
    role, code_column, name_column, scope = spec
    registry = info.get("quantity_registry") or {}
    columns = info.get("column_names") or []
    fields = {column: "scope" for column in columns if _SEMANTIC.search(column)}
    for key, semantic_role in (("unit_column", "unit"), ("operator_column", "operator"),
                               ("formula_column", "formula")):
        if registry.get(key):
            fields[registry[key]] = semantic_role
    # Source expressions are common in measure registries even without a
    # column called formula. Preserve them rather than losing the calculation.
    if "formula" not in fields.values():
        for column in columns:
            if re.search(r"(?:^|_)(?:formula|expression|source_field)$|公式|表达式", column, re.I):
                fields[column] = "formula"
    descriptions = [column for column in columns if _DEFINITION.search(column)
                    and column not in fields and column not in (code_column, name_column)]
    dedicated = [column for column in descriptions if column.casefold() not in ("description", "desc")]
    # A blank business definition does not license substituting an unrelated
    # generic import note, JSON blob, or classification code as its definition.
    generic = [column for column in descriptions if column not in dedicated]
    # A generic note can still contain an explicit business qualifier. Do not
    # hide that conflicting definition just because a dedicated column exists.
    generic = [column for column in generic if not dedicated or re.search(
        r"口径|定义|公式|计算|取值|(?:不|仅|只)含|含税|税前|税后|"
        r"definition|calculation|includes?|excludes?", text(row.get(column)), re.I)
        or (text(row.get(name_column)) and text(row.get(name_column)) in text(row.get(column)))]
    descriptions = dedicated + generic
    descriptions = [column for column in sorted(descriptions) if text(row.get(column))
                    and meaningful_description(row[column], row.get(name_column))]
    description = "\n".join(normalized(row[column]) for column in descriptions)
    identity = {column: text(row.get(column)) for column in scope}
    reference = {"source_table": table, **identity}
    reference.update({column: text(row[column]) for column in columns
                      if _REFERENCE.search(column) and text(row.get(column))})
    attributes = {}
    for column, kind in sorted(fields.items()):
        if kind != "formula":
            value = normalized(row.get(column))
            if kind == "operator":
                value = {"average": "avg"}.get(value.casefold(), value.casefold())
            if column.casefold().endswith("_json") and value:
                try:
                    value = json.dumps(json.loads(value), ensure_ascii=False, sort_keys=True)
                except ValueError:
                    pass
            attributes[kind if kind in ("unit", "operator") else column.casefold()] = value
    return {"table": table, "info": info, "row": row, "role": role,
            "code_column": code_column, "name_column": name_column,
            "code": text(row.get(code_column)), "label": text(row.get(name_column)),
            "identity": identity, "reference_scope": reference, "record_id": record_id,
            "fields": fields, "attributes": attributes, "description": description,
            "description_text": "\n".join(text(row[column]) for column in descriptions),
            "description_columns": descriptions}


def meaningful_description(value, label):
    value = text(value)
    if not value or normalized(value) == normalized(label):
        return False
    if value[:1] in ("{", "[") or re.fullmatch(r"[A-Za-z_]+\d+", value):
        return False
    if re.fullmatch(r"(?:旧|历史)?.{0,8}(?:迁移|补录)|(?:imported|migrated)(?:\s+\w+){0,4}", value, re.I):
        return False
    return True


def canonicalize(entries):
    """Return stable groups plus per-record normalized formula signatures.

    Process referenced definitions first. Ambiguous/cyclic references retain
    a local identity instead of silently merging across a tenant boundary.
    """
    by_code = defaultdict(list)
    for entry in entries:
        by_code[entry["code"]].append(entry)
    for entry in entries:
        entry["formulas"], entry["dependencies"] = [], {}
        for column, kind in entry["fields"].items():
            if kind != "formula":
                continue
            raw = text(entry["row"].get(column))
            parsed = parse_calculation(raw)
            entry["formulas"].append((column, raw, parsed))
            symbols = [item["symbol"] for item in parsed.get("symbols", [])]
            if parsed.get("status") != "parsed":
                # Opaque SQL/text is not a parsed formula, but a local registry
                # code inside it must not accidentally become a global symbol.
                symbols = re.findall(r"\b[A-Za-z_][A-Za-z_0-9]*\b", raw)
            for symbol in symbols:
                candidates = by_code.get(symbol, [])
                if candidates:
                    entry["dependencies"][symbol] = [candidate for candidate in candidates
                        if all(text(entry["row"].get(key)) == value
                               for key, value in candidate["identity"].items())]
    pending = {entry["record_id"]: entry for entry in entries}
    assigned, groups, formula_signatures, formula_bindings = {}, {}, {}, {}
    while pending:
        ready = [entry for entry in pending.values() if all(
            target["record_id"] in assigned for targets in entry["dependencies"].values()
            for target in targets)]
        cyclic = not ready
        if cyclic:
            ready = list(pending.values())
        base_groups = defaultdict(list)
        for entry in ready:
            formula, bindings = _formula_identity(entry, assigned)
            formula_signatures[entry["record_id"]] = formula
            formula_bindings[entry["record_id"]] = bindings
            base = {"role": entry["role"], "label": normalized(entry["label"]),
                    "attributes": entry["attributes"], "formula": formula}
            if cyclic or (not entry["description"] and not any(entry["attributes"].values())
                          and not any(raw for _, raw, _ in entry["formulas"])):
                # A bare name has no calculation/definition evidence. Dimension
                # namespaces may still share a registered code across tenants.
                base["unresolved_identity"] = {"table": entry["table"], "code": entry["code"]}
                if entry["role"] != "Dimension" or cyclic:
                    base["unresolved_identity"]["scope"] = entry["identity"]
            base_groups[digest(base)].append((entry, base))
        for members in base_groups.values():
            descriptions = {entry["description"] for entry, _ in members if entry["description"]}
            for entry, base in members:
                description = entry["description"]
                if not description and len(descriptions) == 1:
                    description = next(iter(descriptions))
                signature = {**base, "description": description}
                type_id = f"type:{entry['role'].casefold()}:shared:{digest(signature)[:24]}"
                assigned[entry["record_id"]] = type_id
                groups.setdefault(type_id, []).append(entry)
                pending.pop(entry["record_id"])
    return groups, assigned, formula_signatures, formula_bindings


def _formula_identity(entry, assigned):
    bindings = {}

    def encode(node):
        if node["kind"] == "symbol":
            symbol = node["symbol"]
            if symbol not in entry["dependencies"]:
                return {"symbol": symbol}
            targets = entry["dependencies"][symbol]
            identities = {assigned.get(target["record_id"]) for target in targets}
            if len(identities) == 1 and None not in identities:
                target = next(iter(identities))
                bindings[symbol] = target
                return {"definition": target}
            return {"unresolved_symbol": symbol, "table": entry["table"],
                    "scope": entry["identity"], "code": entry["code"]}
        return {key: ([encode(child) for child in value] if key == "arguments" else value)
                for key, value in node.items()}

    formulas = []
    for _, raw, parsed in entry["formulas"]:
        if parsed.get("status") == "parsed":
            formulas.append(encode(parsed["expression_tree"]))
        else:
            opaque = {"text": normalized(raw)}
            symbols = [symbol for symbol in re.findall(r"\b[A-Za-z_][A-Za-z_0-9]*\b", raw)
                       if symbol in entry["dependencies"]]
            if symbols:
                opaque["registry_references"] = [encode({"kind": "symbol", "symbol": symbol})
                                                 for symbol in symbols]
            formulas.append(opaque)
    return formulas, bindings
