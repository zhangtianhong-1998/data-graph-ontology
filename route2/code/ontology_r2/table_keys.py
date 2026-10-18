"""Scope columns and per-table keys from full-table counts.

A scope column appears in several tables with a few shared values and makes
otherwise repeated codes unique, as a tenant column does. A key is the
smallest column set that is unique inside that scope. Column names decide
neither; they only break ties between equally unique columns.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict

from .storage import qi


_EXCLUDED_ROLES = {"empty", "audit_time", "audit_metadata", "sensitive"}
_CODE_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")


def annotate_table_keys(data, *, max_scope_values=16, max_key_chars=64, chunk=24):
    """Set ``table_keys`` on every table and ``scope_columns`` on ``data``."""
    report = {"scope_columns": [], "tables": {}}
    tables = getattr(data, "tables", {})
    if not hasattr(data, "db") or not tables:
        data.scope_columns = []
        return report
    columns = {name: _usable_columns(info) for name, info in tables.items()}
    stats = {name: _column_stats(data, info, columns[name], chunk) for name, info in tables.items()}
    scope = _scope_columns(data, tables, columns, stats, max_scope_values)
    data.scope_columns = scope
    report["scope_columns"] = list(scope)
    for name, info in sorted(tables.items()):
        keys = _table_keys(data, info, columns[name], stats[name], scope, max_key_chars, chunk)
        info["table_keys"] = keys
        report["tables"][name] = keys
    return report


def key_scope(info, column):
    """Columns that, with ``column``, identify one row; ``None`` if ``column`` is no key."""
    keys = (info or {}).get("table_keys") or {}
    for key in keys.get("keys", ()):
        if column in key["columns"]:
            return [*key["scope"], *[item for item in key["columns"] if item != column]]
    return None


def key_columns(info):
    keys = (info or {}).get("table_keys") or {}
    found = set(keys.get("scope_columns", ()))
    for key in keys.get("keys", ()):
        found.update(key["columns"])
        found.update(key["scope"])
    return found


def business_key(info):
    keys = (info or {}).get("table_keys") or {}
    return keys.get("business_key")


def _usable_columns(info):
    names = list(info.get("column_names") or ())
    try:
        from .column_roles import classify_columns
        roles = {item["column"]: item["role"] for item in classify_columns(info)}
    except Exception:
        roles = {}
    excluded = set(info.get("semantic_excluded_columns") or ())
    return [name for name in names if roles.get(name) not in _EXCLUDED_ROLES and name not in excluded]


def _column_stats(data, info, columns, chunk):
    table = qi(info["sql_name"])
    stats = {}
    for start in range(0, len(columns), chunk):
        part = columns[start:start + chunk]
        exprs = []
        for column in part:
            field = qi(column)
            usable = f"{field} IS NOT NULL AND trim(CAST({field} AS VARCHAR)) <> ''"
            exprs.extend([
                f"count(*) FILTER (WHERE {usable})",
                f"count(DISTINCT {field}) FILTER (WHERE {usable})",
                f"max(length(CAST({field} AS VARCHAR)))",
                f"count(*) FILTER (WHERE {usable} AND regexp_matches(CAST({field} AS VARCHAR), '^[0-9]+$'))",
            ])
        row = data.db.execute(f"SELECT {', '.join(exprs)} FROM {table}").fetchone()
        for index, column in enumerate(part):
            usable, distinct, longest, digits = row[index * 4: index * 4 + 4]
            stats[column] = {"usable": int(usable or 0), "distinct": int(distinct or 0),
                             "max_chars": int(longest or 0), "digits": int(digits or 0)}
    return stats


def _scope_columns(data, tables, columns, stats, max_scope_values):
    owners = defaultdict(list)
    for name, cols in columns.items():
        for column in cols:
            owners[column].append(name)
    minimum = max(2, math.ceil(len(tables) * 0.25))
    ranked = []
    for column, names in sorted(owners.items()):
        if len(names) < minimum:
            continue
        counts = [stats[name][column]["distinct"] for name in names]
        if max(counts) > max_scope_values or max(counts) < 2:
            continue
        values = set()
        for name in names:
            info = tables[name]
            field = qi(column)
            values.update(str(item) for (item,) in data.db.execute(
                f"SELECT DISTINCT {field} FROM {qi(info['sql_name'])} WHERE {field} IS NOT NULL "
                f"AND trim(CAST({field} AS VARCHAR)) <> '' LIMIT ?", [max_scope_values + 1]).fetchall())
            if len(values) > max_scope_values:
                break
        if len(values) > max_scope_values:
            continue
        split = sum(_splits_duplicates(data, tables[name], columns[name], stats[name], column)
                    for name in names)
        if split:
            ranked.append((split, len(names), column))
    # Several columns can split repeated codes by coincidence. The one that
    # does so in the most tables is the partition; the rest stay ordinary.
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [ranked[0][2]] if ranked else []


def _splits_duplicates(data, info, columns, stats, scope, limit=32):
    rows = info.get("rows") or 0
    table = qi(info["sql_name"])
    for column in columns[:limit]:
        if column == scope:
            continue
        item = stats.get(column)
        if not item or item["usable"] != rows or item["distinct"] >= rows or item["distinct"] < 2:
            continue
        paired = data.db.execute(
            f"SELECT count(*) FROM (SELECT DISTINCT {qi(scope)}, {qi(column)} FROM {table})").fetchone()[0]
        if paired == rows:
            return True
    return False


def _table_keys(data, info, columns, stats, scope, max_key_chars, chunk):
    rows = info.get("rows") or 0
    local_scope = [column for column in scope if column in columns]
    result = {"scope_columns": local_scope, "keys": [], "business_key": None, "surrogate_key": None}
    if rows < 2:
        return result
    candidates = [column for column in columns
                  if column not in local_scope and stats[column]["usable"] == rows
                  and 0 < stats[column]["max_chars"] <= max_key_chars]
    table = qi(info["sql_name"])
    scoped = {}
    if local_scope:
        scope_sql = ", ".join(qi(column) for column in local_scope)
        for start in range(0, len(candidates), chunk):
            part = candidates[start:start + chunk]
            exprs = [f"(SELECT count(*) FROM (SELECT DISTINCT {scope_sql}, {qi(column)} FROM {table}))"
                     for column in part]
            row = data.db.execute("SELECT " + ", ".join(exprs)).fetchone()
            scoped.update(zip(part, row))
    keys = []
    for column in candidates:
        if stats[column]["distinct"] == rows:
            keys.append(_key(column, [], stats[column], rows))
        elif local_scope and scoped.get(column) == rows:
            keys.append(_key(column, local_scope, stats[column], rows))
    if not any(item["kind"] == "business" and _coded(item["columns"][-1]) for item in keys):
        keys.extend(_pair_keys(data, info, candidates, stats, local_scope, rows))
    keys.sort(key=_key_rank)
    result["keys"] = keys
    result["business_key"] = next((item for item in keys if item["kind"] == "business"), None)
    result["surrogate_key"] = next((item for item in keys if item["kind"] == "surrogate"), None)
    return result


def _pair_keys(data, info, candidates, stats, local_scope, rows, limit=40):
    table = qi(info["sql_name"])
    # A composite key is a namespace code plus a member code, such as a
    # dimension code and a member code. Other pairs are coincidental.
    namespaces = sorted((column for column in candidates
                         if 2 <= stats[column]["distinct"] <= max(2, rows // 2)
                         and stats[column]["digits"] == 0 and _coded(column)),
                        key=lambda column: (stats[column]["distinct"], column))
    members = sorted((column for column in candidates
                      if stats[column]["distinct"] > 1 and _coded(column)),
                     key=lambda column: (-stats[column]["distinct"], column))
    found, checked, seen = [], 0, set()
    for first in namespaces:
        for second in members:
            if second == first or checked >= limit or frozenset((first, second)) in seen:
                continue
            seen.add(frozenset((first, second)))
            checked += 1
            parts = ", ".join(qi(column) for column in [*local_scope, first, second])
            distinct = data.db.execute(
                f"SELECT count(*) FROM (SELECT DISTINCT {parts} FROM {table})").fetchone()[0]
            if distinct == rows:
                # The coarsest namespace that works is the key; later unique
                # pairs are coincidences of the same rows.
                key = _key(second, local_scope, stats[second], rows)
                key["columns"] = [first, second]
                return [key]
    return found


def _key(column, scope, item, rows):
    numeric = item["digits"] == item["usable"]
    return {"columns": [column], "scope": list(scope),
            "kind": "surrogate" if numeric else "business",
            "max_chars": item["max_chars"]}


def _coded(column):
    return bool(_CODE_SHAPE.match(column)) and bool(re.search(r"code|编码|代码", column, re.I))


def _key_rank(key):
    column = key["columns"][-1]
    return (key["kind"] != "business", not _coded(column), len(key["columns"]),
            key["max_chars"] if len(key["columns"]) == 1 else 0, column)
