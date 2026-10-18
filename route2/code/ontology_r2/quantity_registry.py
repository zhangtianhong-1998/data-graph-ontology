"""Find definition tables whose rows are measures or metrics.

The decision uses column shape and full-table counts. It does not read a
dataset mapping and it does not treat a table name as a type.
"""

from __future__ import annotations

import re

from .storage import qi


_CODE = re.compile(r"(?:^|_)(?:code|id)$|编码|代码", re.I)
_NAME = re.compile(r"(?:^|_)(?:name|title|label|cn_name)$|名称|名字|标题", re.I)
_FORMULA = re.compile(r"formula|expression|expr|calculation|公式|计算式", re.I)
_UNIT = re.compile(r"(?:^|_)(?:unit|uom)$|^单位$|计量单位", re.I)
_OPERATOR = re.compile(
    r"(?:^|_)(?:operator|aggregation|agg_type|inference_type)$|计算方式|聚合方式|运算方式", re.I)
_METRIC_TOKEN = re.compile(r"metric|indicator|指标", re.I)
_MEASURE_TOKEN = re.compile(r"measure|度量", re.I)
_HIERARCHY = re.compile(
    r"parent_.*(?:code|id)|(?:^|_)level_no$|code_path|上级编码|层级", re.I)


def annotate_quantity_registries(data):
    """Attach ``quantity_registry`` to tables that define one quantity per code.

    Returns a report. Tables that do not match are left unchanged.
    """
    report = {"tables": []}
    if not hasattr(data, "db"):
        return report
    for name, info in sorted(getattr(data, "tables", {}).items()):
        try:
            found = _classify_table(data, name, info)
        except Exception as exc:
            report["tables"].append({"table": name, "role": None, "reason": "query_failed",
                                    "error_type": type(exc).__name__})
            continue
        if found is None:
            continue
        info["quantity_registry"] = found
        report["tables"].append({"table": name, **found})
    return report


def _column_names(info):
    names = list(info.get("column_names") or ())
    if names:
        return names
    return [item.get("column_name") for item in info.get("columns", ()) if item.get("column_name")]


def _classify_table(data, name, info):
    columns = _column_names(info)
    if len(columns) < 2 or any(_HIERARCHY.search(column) for column in columns):
        return None
    codes = sorted((column for column in columns if _CODE.search(column)),
                   key=lambda column: (0 if re.search(r"code|编码|代码", column, re.I) else 1, column))
    names = [column for column in columns if _NAME.search(column) and column not in codes]
    if not codes or not names:
        return None
    formulas = [column for column in columns if _FORMULA.search(column)]
    operators = [column for column in columns if _OPERATOR.search(column)]
    units = [column for column in columns if _UNIT.search(column)]
    quantity_named = any(_METRIC_TOKEN.search(column) or _MEASURE_TOKEN.search(column)
                         for column in columns)
    table_name = str(info.get("table_name") or name.rsplit(".", 1)[-1])
    if not operators and not formulas and not quantity_named:
        return None
    code, scope = _registry_code(data, info, codes)
    if code is None:
        return None
    label = _dependent_name(data, info, code, names, scope)
    if label is None:
        return None
    operator = operators[0] if operators else None
    formula = formulas[0] if formulas else None
    operator_values = _distinct_count(data, info, operator) if operator else None
    formula_share = _expression_share(data, info, formula) if formula else 0
    # Column names of the key and label come first; the table name only
    # settles a key named plainly "code" or "id".
    metric_named = bool(_METRIC_TOKEN.search(code) or _METRIC_TOKEN.search(label))
    measure_named = bool(_MEASURE_TOKEN.search(code) or _MEASURE_TOKEN.search(label))
    if not metric_named and not measure_named:
        metric_named = bool(_METRIC_TOKEN.search(table_name))
        measure_named = bool(_MEASURE_TOKEN.search(table_name))
    metric_shaped = bool(metric_named and not measure_named and (
        formula or formula_share >= 0.5 or units))
    # An aggregation column is a calculation attribute. It does not turn a
    # metric definition table into a measure, and a mixed table is not locked.
    if metric_named and measure_named:
        return None
    if metric_shaped or (formula and formula_share >= 0.5 and not measure_named):
        role, reason = "Metric", "formula_definition_keeps_metric_role"
    elif operator and operator_values is not None and 1 <= operator_values <= 24:
        role, reason = "Measure", "unique_code_name_and_operator"
    elif measure_named and (operator or formula or units):
        role, reason = "Measure", "unique_code_name_and_measure_column"
    else:
        return None
    usable, distinct = _cardinality(data, info, code)
    return {"role": role, "code_column": code, "name_column": label,
            "operator_column": operator, "formula_column": formula,
            "unit_column": units[0] if units else None, "scope_columns": scope,
            "usable_codes": usable, "distinct_codes": distinct, "reason": reason,
            "table": name}


def _registry_code(data, info, codes):
    """A business code unique alone or within the table's scope beats a row number."""
    keys = (info.get("table_keys") or {}).get("keys") or []
    if keys:
        keyed = {key["columns"][0]: key for key in keys
                 if len(key["columns"]) == 1 and key["kind"] == "business"}
        ordered = [column for column in codes if column in keyed]
        if ordered:
            return ordered[0], list(keyed[ordered[0]]["scope"])
        return None, []
    column = _most_unique(data, info, codes)
    if column is None or _all_digits(data, info, column):
        return None, []
    return column, []


def _all_digits(data, info, column):
    field, table = qi(column), qi(info["sql_name"])
    usable, digits = data.db.execute(
        f"SELECT count(*) FILTER (WHERE {field} IS NOT NULL AND trim(CAST({field} AS VARCHAR)) <> ''), "
        f"count(*) FILTER (WHERE regexp_matches(CAST({field} AS VARCHAR), '^[0-9]+$')) "
        f"FROM {table}").fetchone()
    return bool(usable) and usable == digits


def _cardinality(data, info, column):
    field = qi(column)
    table = qi(info["sql_name"])
    usable, distinct = data.db.execute(
        f"SELECT count(*) FILTER (WHERE {field} IS NOT NULL AND "
        f"trim(CAST({field} AS VARCHAR)) <> ''), "
        f"count(DISTINCT {field}) FILTER (WHERE {field} IS NOT NULL AND "
        f"trim(CAST({field} AS VARCHAR)) <> '') FROM {table}").fetchone()
    return int(usable or 0), int(distinct or 0)


def _most_unique(data, info, columns):
    best = None
    for column in columns:
        usable, distinct = _cardinality(data, info, column)
        if usable >= 2 and distinct == usable:
            return column
    return best


def _dependent_name(data, info, code, names, scope=()):
    code_sql, table = qi(code), qi(info["sql_name"])
    group = ", ".join(qi(column) for column in [*scope, code])
    for column in names:
        name_sql = qi(column)
        conflicts, named = data.db.execute(
            f"SELECT count(*) FILTER (WHERE names > 1), count(*) "
            f"FROM (SELECT {group}, count(DISTINCT {name_sql}) AS names "
            f"FROM {table} WHERE {code_sql} IS NOT NULL AND trim(CAST({code_sql} AS VARCHAR)) <> '' "
            f"GROUP BY {group})").fetchone()
        filled = data.db.execute(
            f"SELECT count(*) FROM {table} WHERE {code_sql} IS NOT NULL AND "
            f"trim(CAST({code_sql} AS VARCHAR)) <> '' AND {name_sql} IS NOT NULL AND "
            f"trim(CAST({name_sql} AS VARCHAR)) <> ''").fetchone()[0]
        if conflicts == 0 and named and filled / named >= 0.9:
            return column
    return None


def _distinct_count(data, info, column):
    field, table = qi(column), qi(info["sql_name"])
    return data.db.execute(
        f"SELECT count(DISTINCT {field}) FROM {table} WHERE {field} IS NOT NULL AND "
        f"trim(CAST({field} AS VARCHAR)) <> ''").fetchone()[0]


def _expression_share(data, info, column):
    field, table = qi(column), qi(info["sql_name"])
    filled, expressions = data.db.execute(
        f"SELECT count(*) FILTER (WHERE {field} IS NOT NULL AND trim(CAST({field} AS VARCHAR)) <> ''), "
        f"count(*) FILTER (WHERE regexp_matches(CAST({field} AS VARCHAR), '[=+*/]') "
        f"OR regexp_matches(CAST({field} AS VARCHAR), ' - ')) FROM {table}").fetchone()
    if not filled:
        return 0
    return expressions / filled
