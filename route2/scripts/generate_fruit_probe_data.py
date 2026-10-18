"""Small, imperfect, entirely synthetic catalog with a separate evaluation oracle.

The ordinary CSV/YAML input contains business records only. The sibling oracle is
for an evaluator, never an extraction input. This does not approximate any real
customer's value distributions; it exercises documented classes of missing and
conflicting evidence in a repeatable small experiment.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import yaml

import generate_fruit_data as base


VERSION = "fruit-probe-v1"
DEFAULT_SEED = 20261016
MIN_ROWS = 2000
DEFAULT_COUNTS = dict(zip((t.name for t in base.TABLES), (
    80, 400, 250, 900, 1100, 60, 10, 800, 80, 20, 40, 80,
    120, 120, 12, 3500, 80, 4, 20, 400, 180, 400, 1344,
)))
API = "fruit_market_api"
CARD = "fruit_dashboard_card"
INPUT = "fruit_api_input_param"
OUTPUT = "fruit_api_output_param"
DIMS = "fruit_dim_definition"
MEMBERS = "fruit_dim_member"
ORG = "fruit_org_dim_data"
METRICS = "fruit_metric_detail"
MEASURES = "fruit_measure_def"
REFS = "fruit_param_ref_rule"
RESOURCE_KIND = {API: "API", CARD: "CARD"}
BUSINESS_KINDS = {"API": "API", "api": "API", "接口": "API",
                  "CARD": "CARD", "card": "CARD", "卡片": "CARD"}
EXPECTED_ROLES = {
    API: "API", CARD: "Card", INPUT: "InputParameter", OUTPUT: "OutputParameter",
    DIMS: "Dimension", MEMBERS: "DimensionMember", ORG: "DimensionMember",
    MEASURES: "Measure", METRICS: "Metric", "fruit_api_tag": "ResourceTag",
    "fruit_op_log": "OperationLog", "fruit_import_staging_temp": "StagingRecord",
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _counts(rows):
    if isinstance(rows, bool) or not isinstance(rows, int) or rows < MIN_ROWS:
        raise ValueError(f"rows must be an integer >= {MIN_ROWS}")
    counts = {name: max(2, int(count * rows / 10000))
              for name, count in DEFAULT_COUNTS.items()}
    counts[DIMS] = 10
    counts["fruit_import_staging_temp"] += rows - sum(counts.values())
    if counts["fruit_import_staging_temp"] < 1:
        raise ValueError("row budget is too small for all table families")
    return counts


def _fields(table):
    fields = [field for field in table.fields if not (table.name == CARD and field == "api_id")]
    fields += [field for field in ("tenant_id", "description", "example") if field not in fields]
    if table.name in (DIMS, MEASURES, METRICS):
        fields += ["business_id"]  # Local accounting group, not an API/Card reference.
    if table.name == ORG:
        fields += ["parent_member_code"]
    if table.name == REFS:
        fields += ["param_direction"]
    if table.name == METRICS:
        fields += ["aggregation"]
        fields = [{"metric_code": "code", "metric_name": "name"}.get(field, field) for field in fields]
    return fields


def _tenant(i):
    return f"T{i % 2 + 1}"


def _business_alias(kind, i):
    return (("API", "接口", "api") if kind == "API" else ("CARD", "卡片", "card"))[i % 3]


def _endpoint(data, table, index, fields):
    fields = list(dict.fromkeys(fields))
    return {"table": table, "row": index + 1, "fields": fields,
            "values": {field: data[table][index][field] for field in fields}}


def _path(value, path):
    if path:
        value = json.loads(value)
        for part in path:
            value = value[part]
    return value


def _build(rows, seed):
    counts = _counts(rows)
    rng = random.Random(seed)
    data = {}
    for table in base.TABLES:
        fields = _fields(table)
        data[table.name] = []
        for i in range(counts[table.name]):
            row = base.make_row(table, i, counts)
            row.pop("api_id", None) if table.name == CARD else None
            row.update(tenant_id=_tenant(i), description=rng.choice((
                "", "", "按会计期汇总", "历史口径补录", "Imported legacy item",
                _json({"note": "月末调整"}), f"CAT{i % 11:02d}")),
                example=rng.choice(("", "[]", "null", "2025-01", "苹果", "1")))
            if table.name in (DIMS, MEASURES, METRICS):
                row["business_id"] = str(i % 7 + 1)
            data[table.name].append({field: str(row.get(field, "")) for field in fields})

    resources = []
    for table in (API, CARD):
        id_field = "fruit_api_id" if table == API else "fruit_card_id"
        for i, row in enumerate(data[table]):
            local_id = str(i // 2 + 1)
            row[id_field] = local_id
            row["tenant_id"] = _tenant(i)
            if table == API:
                row.update(api_code=f"SVC{i // 2 + 1:03d}",
                           api_name=f"{base.FRUITS[i % 10][1]}经营查询{i // 10 + 1}",
                           api_description="按产区、品类和会计期查询销售金额、销量和同期变化。" if i % 4 else "",
                           api_url=f"/offline/catalog/query/{local_id}",
                           request_example=_json({"region": "B01", "period": "2025-01"}),
                           response_example=_json({"amount": 1250.0, "currency": "CNY"}))
            else:
                row.update(card_code=f"VIEW{i // 2 + 1:03d}",
                           card_name=f"{base.REGIONS[i % 5][1]}销售概览{i // 5 + 1}",
                           card_description="可选择地区、品类和月份，比较实际金额与预算。" if i % 4 else "",
                           card_config_json=_json({"layout": "table", "period": "M", "currency": "CNY"}))
            resources.append((table, i))

    for i, row in enumerate(data[DIMS]):
        dim = i // 2
        row.update(tenant_id=_tenant(i), dim_code=f"DIM{dim + 1:04d}",
                   dim_cn_name=base.DIMENSIONS[dim][1], dim_en_name=base.DIMENSIONS[dim][2],
                   dim_description=("用于地区汇总及下钻" if dim == 1 else "") if i % 3 else "旧目录迁移",
                   dim_level_count="3", physical_table_name=MEMBERS,
                   primary_code_field="member_code", primary_name_field="member_cn_name",
                   hierarchy_json=_json({"levels": ["全部", "分组", "明细"]}))

    for table in (MEMBERS, ORG):
        counters = Counter()
        for i, row in enumerate(data[table]):
            dim = data[DIMS][i % len(data[DIMS])]
            scope = (dim["tenant_id"], dim["dim_code"])
            n = counters[scope]
            counters[scope] += 1
            if n == 0:
                code, parent, level = "ROOT", "", 0
            elif n <= 3:
                code, parent, level = f"B{n:02d}", "ROOT", 1
            else:
                code, parent, level = f"M{n - 3:04d}", f"B{(n - 4) % 3 + 1:02d}", 2
            prefix = "经营" if table == MEMBERS else "仓储"
            name = (prefix + dim["dim_cn_name"] + "全部" if level == 0 else
                    prefix + base.REGIONS[(n - 1) % 5][1] + "分部" if level == 1 else
                    prefix + base.FRUITS[(n - 4) % 10][1] + f"{n - 3}组")
            row.update(tenant_id=scope[0], dim_code=scope[1], member_code=code,
                       member_cn_name=name, member_en_name=f"{prefix}_{scope[1]}_{code}",
                       parent_member_code=parent, level_no=str(level),
                       code_path="/".join(["ROOT", code] if level == 1 else
                                          ["ROOT", parent, code] if level == 2 else ["ROOT"]),
                       synonym_text=name + ("," + name.replace("经营", "") if level == 2 else ""))
            if table == MEMBERS:
                row["member_description"] = "" if i % 3 else "停用项请按历史期间查询"
            else:
                row["source_system"] = "WAREHOUSE_EXPORT"
        # Errors are concentrated enough to be observable in bounded early samples.
        data[table][40]["parent_member_code"] = "PARENT_MISSING"
        data[table][41]["parent_member_code"] = data[table][41]["member_code"]
        data[table][42]["member_cn_name"] = ""

    measure_templates = (
        ("累计金额", "YTD_AMT", "SUM", "元", "sales_amount"),
        ("月度金额", "MTD_AMT", "SUM", "元", "sales_amount"),
        ("累计同比", "YOY(YTD)", "RATIO", "%", "current_amount / previous_amount - 1"),
        ("季度预算", "Budget(Q)", "SUM", "元", "budget_amount"),
        ("半年预实", "Actual/Forecast(H1)", "SUM", "元", "actual_amount + forecast_amount"),
        ("销量", "SALES_QTY", "SUM", "吨", "sales_volume"),
        ("均价", "AVG_PRICE", "RATIO", "元/吨", "sales_amount / sales_volume"),
        ("退货金额", "RETURN_AMT", "SUM", "元", "return_amount"),
    )
    for i, row in enumerate(data[MEASURES]):
        label, standard, operator, unit, formula = measure_templates[(i // 2) % len(measure_templates)]
        period = {"YTD_AMT": "YTD", "MTD_AMT": "MTD", "YOY(YTD)": "YTD",
                  "Budget(Q)": "Q", "Actual/Forecast(H1)": "H1", "SALES_QTY": "MTD",
                  "AVG_PRICE": "MTD", "RETURN_AMT": "MTD"}[standard]
        row.update(measure_code=f"MES{i // 2 + 1:04d}", measure_name=label,
                   standard_name=standard, parameter_name=f"value_{i // 2 + 1}",
                   measure_description=(f"统计期间：{period}；计量单位：{unit}" if i % 5 == 0 else ""),
                   inference_type=operator, unit=unit, source_field=formula,
                   metric_code="", period=period,
                   budget_flag=str(int(standard == "Budget(Q)")),
                   forecast_flag=str(int(standard == "Actual/Forecast(H1)")),
                   actual_flag=str(int(standard != "Budget(Q)")),
                   target_flag="0", remain_flag="0", estimate_flag="0", rank_flag="0")
    measures_by_tenant = {tenant: [row for row in data[MEASURES] if row["tenant_id"] == tenant]
                          for tenant in ("T1", "T2")}
    metric_measure = {}
    metric_templates = (("累计销售收入", "YTD_AMT"), ("月度销售收入", "MTD_AMT"),
                        ("销售同比", "YOY(YTD)"), ("季度销售预算", "Budget(Q)"),
                        ("半年预实金额", "Actual/Forecast(H1)"), ("销售均价", "AVG_PRICE"))
    for i, row in enumerate(data[METRICS]):
        pool = measures_by_tenant[row["tenant_id"]]
        label, standard = metric_templates[(i // 2) % len(metric_templates)]
        m = next(item for item in pool if item["standard_name"] == standard)
        formula = m["measure_code"]
        if standard == "AVG_PRICE":
            amount = next(item for item in pool if item["standard_name"] == "MTD_AMT")
            quantity = next(item for item in pool if item["standard_name"] == "SALES_QTY")
            formula = f"{amount['measure_code']} / {quantity['measure_code']}"
        row.update(metric_code=f"MET{i // 2 + 1:04d}", metric_name=label,
                   metric_definition=f"{label}，单位{m['unit']}，采用月报发布口径" if i % 7 == 0 else "",
                   calculation_formula=formula if i % 11 else "以财务发布口径为准",
                   aggregation="SUM", unit=m["unit"], setup_purpose="经营月报")
        if standard in ("AVG_PRICE", "YOY(YTD)"):
            row["aggregation"] = "RATIO"
        metric_measure[(row["tenant_id"], row["metric_code"])] = m["measure_code"]
    metrics_by_tenant = {tenant: [row for row in data[METRICS] if row["tenant_id"] == tenant]
                         for tenant in ("T1", "T2")}
    for i, row in enumerate(data["fruit_metric_attr"]):
        metric = metrics_by_tenant[row["tenant_id"]][(i // 2) % len(metrics_by_tenant[row["tenant_id"]])]
        row.update(metric_code=metric["metric_code"], measure_code=metric_measure[(row["tenant_id"], metric["metric_code"])],
                   metric_formula=metric["calculation_formula"], synonym_text=metric["metric_name"].replace("销售毛利率", "销毛率"),
                   attr_description="" if i % 3 else "适用月报", unit=metric["unit"])

    parameter_rows = []
    categories = {}
    for table, direction in ((INPUT, "IN"), (OUTPUT, "OUT")):
        counters = Counter()
        id_field = "fruit_input_param_id" if table == INPUT else "fruit_output_param_id"
        specs = (("region_code", "产区", "dimension"), ("fruit_code", "品类", "dimension"),
                 ("metric_code", "分析项目", "metric"), ("period", "会计期", "period"),
                 ("value", "金额", "measure"), ("currency", "币种", "currency"))
        for i, row in enumerate(data[table]):
            owner_table, owner_i = resources[i % len(resources)]
            owner = data[owner_table][owner_i]
            owner_id = owner["fruit_api_id" if owner_table == API else "fruit_card_id"]
            tenant = owner["tenant_id"]
            counters[tenant] += 1
            field, cn, category = specs[(i // len(resources) + i % 3) % len(specs)]
            row.update(tenant_id=tenant, **{id_field: str(counters[tenant])},
                       business_id=owner_id, business_type=_business_alias(RESOURCE_KIND[owner_table], i),
                       param_name=field, param_cn_name=cn, param_type="number" if category == "measure" else "string",
                       param_description="" if i % 4 else f"{cn}，沿用月报取值")
            parameter_rows.append((table, i, direction))
            categories[(table, i)] = category
    hints = {}
    for i, row in enumerate(data[REFS]):
        param_table, pi, direction = parameter_rows[i % len(parameter_rows)]
        param = data[param_table][pi]
        pid = param["fruit_input_param_id" if param_table == INPUT else "fruit_output_param_id"]
        tenant = param["tenant_id"]
        category = categories[(param_table, pi)]
        row.update(tenant_id=tenant, business_id=param["business_id"], business_type=param["business_type"],
                   param_id=pid, param_direction=direction, rule_description="" if i % 4 else "以目录维护的有效范围为准")
        if category in ("metric", "measure"):
            target_table = METRICS if category == "metric" else MEASURES
            pool = metrics_by_tenant[tenant] if category == "metric" else measures_by_tenant[tenant]
            target = pool[(i // 2) % len(pool)]
            key = "metric_code" if category == "metric" else "measure_code"
            row["source_type"] = (("metric", "指标", "Metric") if category == "metric" else ("measure", "度量", "Measure"))[i % 3]
            row["source_field"] = target[key]
            codes = [target[key], pool[(i // 2 + 1) % len(pool)][key]]
            row["field_rule"] = _json({"allowed": codes}) if i % 4 == 0 else _json(codes)
            hints[i] = (target_table, key, "source_field", [], None)
            if i % 23 == 3:
                row["source_field"] = _json({"code": target[key]})
                hints[i] = (target_table, key, "source_field", ["code"], None)
            elif i % 23 == 4:
                key = "metric_name" if category == "metric" else "measure_name"
                row["source_field"] = target[key]
                hints[i] = (target_table, key, "source_field", [], None)
        elif category == "dimension":
            dim_code = "DIM0002" if param["param_name"] == "region_code" else "DIM0001"
            pool = [m for m in data[MEMBERS] if m["tenant_id"] == tenant and m["dim_code"] == dim_code and m["level_no"] == "2"]
            codes = [pool[i % len(pool)]["member_code"], pool[(i + 1) % len(pool)]["member_code"]]
            row.update(source_type=dim_code, source_field="member_code", field_rule=_json(codes))
            if i % 4 == 0:
                row["field_rule"] = _json({"allowed": codes})
            hints[i] = (DIMS, "dim_code", "source_type", [], dim_code)
        else:
            row.update(source_type="会计期" if category == "period" else "currency",
                       source_field="accounting_period" if category == "period" else "currency_code",
                       field_rule=_json(["2025-01", "2025-02"] if category == "period" else ["CNY", "USD"]))
        # Fixed cases in the sample head, and again near the tail, avoid sampling away all failures.
        case = i if i < 16 else i - len(data[REFS]) + 16 if i >= len(data[REFS]) - 11 else -1
        if case == 5:
            row.update(source_type="metric", source_field="MET_MISSING", field_rule='["MET_MISSING"]')
            hints[i] = (METRICS, "metric_code", "source_field", [], None)
        elif case == 6:
            row["field_rule"] = '["BROKEN",'
        elif case == 7:
            row["source_type"] = ""
            hints.pop(i, None)
        elif case == 8:
            row["source_field"] = ""
            hints.pop(i, None)
        elif case == 9:
            row.update(source_type="legacy_unknown", source_field="1")
            hints.pop(i, None)
        elif case == 10:
            row["tenant_id"] = ""
        elif case == 11:
            row["param_id"] = "PARAM_MISSING"
        elif case == 12:
            row["param_direction"] = ""
        elif case == 13:
            row.update(source_type="metric", source_field=metrics_by_tenant[tenant][0]["metric_code"],
                       field_rule=_json([metrics_by_tenant[tenant][0]["metric_code"], "MET_MISSING"]))
            hints[i] = (METRICS, "metric_code", "source_field", [], None)

    for table in ("fruit_api_tag", "fruit_op_log"):
        for i, row in enumerate(data[table]):
            owner_table, owner_i = resources[i % len(resources)]
            owner = data[owner_table][owner_i]
            row.update(tenant_id=owner["tenant_id"], business_id=owner["fruit_api_id" if owner_table == API else "fruit_card_id"],
                       business_type=_business_alias(RESOURCE_KIND[owner_table], i))
            if table == "fruit_op_log":
                row.update(parent_business_id=row["business_id"], parent_business_type=row["business_type"])
    for i, row in enumerate(data["fruit_dim_field"]):
        dim = data[DIMS][i % 10]
        level = (i // 10) % 3
        field = "member_code" if (i // 30) % 2 == 0 else "member_cn_name"
        row.update(tenant_id=dim["tenant_id"], dim_code=dim["dim_code"], level_no=str(level),
                   physical_field_name=field, output_name=field, field_cn_name="编码" if field == "member_code" else "名称",
                   field_description="", synonym_text="")
    for i, row in enumerate(data["fruit_synonym_dict"]):
        member = data[MEMBERS][i % len(data[MEMBERS])]
        row.update(tenant_id=member["tenant_id"], word=member["member_cn_name"].replace("经营", ""),
                   attr=_json({"dim_code": member["dim_code"], "member_code": member["member_code"]}),
                   type="历史简称", scope=member["dim_code"])

    # Repair remaining base-generator references to actual tenant-scoped registries.
    for table, records in data.items():
        if table in (DIMS, MEMBERS, ORG, MEASURES, METRICS, INPUT, OUTPUT, REFS, "fruit_metric_attr", "fruit_dim_field"):
            continue
        for i, row in enumerate(records):
            if "metric_code" in row:
                row["metric_code"] = metrics_by_tenant[row["tenant_id"]][i % len(metrics_by_tenant[row["tenant_id"]])]["metric_code"]
            if "measure_code" in row:
                row["measure_code"] = measures_by_tenant[row["tenant_id"]][i % len(measures_by_tenant[row["tenant_id"]])]["measure_code"]
            if "dim_code" in row:
                row["dim_code"] = f"DIM{i % 5 + 1:04d}"
            if table == CARD:
                measure_code = metric_measure[(row["tenant_id"], row["metric_code"])]
                row["measure_name"] = next(m["measure_name"] for m in measures_by_tenant[row["tenant_id"]] if m["measure_code"] == measure_code)
    # Generic legacy headers exercise structural typing without embedding the answer
    # in metric_code/metric_name. References continue to use the unchanged codes.
    for row in data[METRICS]:
        row["code"] = row.pop("metric_code")
        row["name"] = row.pop("metric_name")
    hints = {i: (table, {"metric_code": "code", "metric_name": "name"}.get(key, key)
                  if table == METRICS else key, field, path, dim)
             for i, (table, key, field, path, dim) in hints.items()}
    return data, counts, hints, rng


def _oracle(data, hints):
    oracle = {"version": VERSION, "row_numbering": "1-based CSV data rows, excluding header",
              "expected_table_roles": EXPECTED_ROLES, "allowed_links": [], "forbidden_links": [],
              "ambiguous_cases": [], "anomaly_ledger": [], "controls": [],
              "limits": ["Synthetic mechanisms, not measured real-data frequencies.",
                         "Type labels are evaluation intent; weak or absent evidence can legitimately remain unresolved.",
                         "Allowed links are scoped record relations, not permission to promote column overlap to business truth.",
                         "The oracle covers resource ownership, parameter references, metric attributes and member hierarchy. Auxiliary source/region/wide-table context fields and inherited descriptive JSON are not exhaustive relation ground truth; do not score unlisted pairs as false."]}

    def anomaly(table, i, field, kind, expected, **extra):
        oracle["anomaly_ledger"].append({"source": _endpoint(data, table, i, [field]),
                                         "kind": kind, "expected_status": expected, **extra})

    def resolve(table, i, field, target_tables, target_field, relation, *, path=(), scope=(), condition=None, value=None):
        row = data[table][i]
        try:
            actual = _path(row[field], path)
        except (ValueError, KeyError, TypeError, IndexError):
            anomaly(table, i, field, "malformed_reference", "unresolved")
            return
        if value is not None:
            if isinstance(actual, str):
                try:
                    actual = json.loads(actual)
                except ValueError:
                    anomaly(table, i, field, "malformed_reference", "unresolved")
                    return
            if not isinstance(actual, list) or value not in actual:
                raise AssertionError("oracle value must be in the actual transformed array")
            actual = value
        if actual == "" or actual is None:
            return
        source_fields = [field] + [s for s, _ in scope]
        if condition:
            source_fields += list(condition)
        candidates = []
        for target_table in target_tables:
            for ti, target in enumerate(data[target_table]):
                if target.get(target_field) != actual:
                    continue
                # Missing scopes are unknown: enumerate all possible scopes, never invent one.
                if any(row[s] and row[s] != target[t] for s, t in scope):
                    continue
                candidates.append((target_table, ti))
        source = _endpoint(data, table, i, source_fields)
        if not candidates:
            anomaly(table, i, field, "dangling_reference", "unresolved", reference_value=actual, relation=relation)
        elif len(candidates) > 1 or any(not row[s] for s, _ in scope):
            oracle["ambiguous_cases"].append({"source": source, "relation": relation,
                "reference_value": actual, "kind": "missing_scope_or_nonunique_name", "expected_status": "unresolved",
                "candidates": [_endpoint(data, t, j, [target_field] + [tfield for _, tfield in scope]) for t, j in candidates]})
        else:
            target_table, ti = candidates[0]
            oracle["allowed_links"].append({"source": source,
                "target": _endpoint(data, target_table, ti, [target_field] + [t for _, t in scope]),
                "relation": relation, "expected_status": "accepted", "reference_field": field,
                "reference_path": list(path), "reference_value": actual,
                "target_field": target_field, "scope_fields": [list(pair) for pair in scope],
                "condition": condition or {}, "array_member": value is not None})

    for table in (INPUT, OUTPUT, "fruit_api_tag", "fruit_op_log", REFS):
        for i, row in enumerate(data[table]):
            kind = BUSINESS_KINDS.get(row["business_type"])
            if kind:
                target_table = API if kind == "API" else CARD
                target_field = "fruit_api_id" if kind == "API" else "fruit_card_id"
                resolve(table, i, "business_id", [target_table], target_field, "resource_owner",
                        scope=(("tenant_id", "tenant_id"),), condition={"business_type": row["business_type"]})
    for table in (MEMBERS, ORG):
        for i, row in enumerate(data[table]):
            resolve(table, i, "dim_code", [DIMS], "dim_code", "member_dimension", scope=(("tenant_id", "tenant_id"),))
            if row["parent_member_code"] == row["member_code"]:
                anomaly(table, i, "parent_member_code", "self_parent_cycle", "rejected")
            else:
                resolve(table, i, "parent_member_code", [table], "member_code", "member_parent",
                        scope=(("tenant_id", "tenant_id"), ("dim_code", "dim_code")))
            if not row["member_cn_name"]:
                anomaly(table, i, "member_cn_name", "missing_display_name", "keep_identity_without_inventing_name")
    for i, row in enumerate(data["fruit_metric_attr"]):
        for key, target_table, relation in (("metric_code", METRICS, "attribute_metric"), ("measure_code", MEASURES, "metric_measure")):
            target_key = "code" if target_table == METRICS else key
            resolve("fruit_metric_attr", i, key, [target_table], target_key, relation, scope=(("tenant_id", "tenant_id"),))
    for i, row in enumerate(data[REFS]):
        direction = row["param_direction"]
        target_tables = [INPUT] if direction == "IN" else [OUTPUT] if direction == "OUT" else [INPUT, OUTPUT]
        # Both parameter tables have distinct field names: preserve all plausible endpoints for missing direction.
        candidates = []
        for t in target_tables:
            key = "fruit_input_param_id" if t == INPUT else "fruit_output_param_id"
            candidates += [(t, j, key) for j, p in enumerate(data[t]) if p[key] == row["param_id"]
                           and (not row["tenant_id"] or p["tenant_id"] == row["tenant_id"])
                           and p["business_id"] == row["business_id"]
                           and BUSINESS_KINDS.get(p["business_type"]) == BUSINESS_KINDS.get(row["business_type"])]
        if direction and len(candidates) == 1 and row["tenant_id"]:
            t, _, key = candidates[0]
            resolve(REFS, i, "param_id", [t], key, "rule_parameter",
                    scope=(("tenant_id", "tenant_id"), ("business_id", "business_id")),
                    condition={"param_direction": direction, "business_type": row["business_type"]})
        elif len(candidates) > 1 or not direction or not row["tenant_id"]:
            oracle["ambiguous_cases"].append({"source": _endpoint(data, REFS, i, ["param_id", "param_direction", "tenant_id", "business_id", "business_type"]),
                "relation": "rule_parameter", "kind": "missing_parameter_scope", "expected_status": "unresolved",
                "candidates": [_endpoint(data, t, j, [key, "tenant_id", "business_id", "business_type"]) for t, j, key in candidates]})
        else:
            anomaly(REFS, i, "param_id", "dangling_parameter", "unresolved")
        if i in hints:
            target_table, key, source, path, dim_code = hints[i]
            resolve(REFS, i, source, [target_table], key, "parameter_reference", path=path,
                    scope=(("tenant_id", "tenant_id"),), condition={"source_type": row["source_type"]})
        elif row["source_type"] in ("", "legacy_unknown") or not row["source_field"]:
            anomaly(REFS, i, "source_type", "unknown_reference_semantics", "unresolved")
        try:
            content = json.loads(row["field_rule"])
        except (ValueError, TypeError):
            anomaly(REFS, i, "field_rule", "malformed_json", "unresolved")
            continue
        path = ["allowed"] if isinstance(content, dict) else []
        values = content.get("allowed") if isinstance(content, dict) else content
        if not isinstance(values, list):
            continue
        kind = row["source_type"]
        if kind in ("metric", "指标", "Metric", "measure", "度量", "Measure"):
            table = METRICS if kind in ("metric", "指标", "Metric") else MEASURES
            key = "code" if table == METRICS else "measure_code"
            for value in dict.fromkeys(values):
                resolve(REFS, i, "field_rule", [table], key, "allowed_parameter_value", path=path,
                        value=value, scope=(("tenant_id", "tenant_id"),), condition={"source_type": kind})
        elif kind.startswith("DIM"):
            # The dimension's physical_table_name identifies the authoritative member source.
            for value in dict.fromkeys(values):
                resolve(REFS, i, "field_rule", [MEMBERS], "member_code", "allowed_dimension_member", path=path,
                        value=value, scope=(("tenant_id", "tenant_id"), ("source_type", "dim_code")),
                        condition={"source_type": kind})

    # Negative controls are explicit wrong alternatives to accepted scoped relations.
    for link in oracle["allowed_links"]:
        if link["relation"] not in ("resource_owner", "member_parent"):
            continue
        target = link["target"]
        source = link["source"]
        wrong_table = (CARD if target["table"] == API else API) if link["relation"] == "resource_owner" else (ORG if target["table"] == MEMBERS else MEMBERS)
        key = ("fruit_card_id" if wrong_table == CARD else "fruit_api_id") if link["relation"] == "resource_owner" else "member_code"
        for j, row in enumerate(data[wrong_table]):
            if row[key] == link["reference_value"] and row["tenant_id"] == source["values"]["tenant_id"]:
                oracle["forbidden_links"].append({"source": source, "target": _endpoint(data, wrong_table, j, [key, "tenant_id"]),
                    "relation": link["relation"], "expected_status": "rejected",
                    "reason": "business_type_conflict" if link["relation"] == "resource_owner" else "unproven_cross_catalog_identity"})
                break
        if len(oracle["forbidden_links"]) >= 80:
            break
    # Ensure cross-catalog hierarchy collisions are represented even with many owner links.
    for table in (MEMBERS, ORG):
        other = ORG if table == MEMBERS else MEMBERS
        for i, row in enumerate(data[table]):
            if row["level_no"] == "1":
                j = next(j for j, r in enumerate(data[other]) if r["tenant_id"] == row["tenant_id"] and r["dim_code"] == row["dim_code"] and r["member_code"] == row["parent_member_code"])
                oracle["forbidden_links"].append({"source": _endpoint(data, table, i, ["tenant_id", "dim_code", "parent_member_code"]),
                    "target": _endpoint(data, other, j, ["tenant_id", "dim_code", "member_code"]),
                    "relation": "member_parent", "expected_status": "rejected", "reason": "unproven_cross_catalog_identity"})
                break
    for relation in sorted({link["relation"] for link in oracle["allowed_links"]}):
        oracle["controls"].extend([link for link in oracle["allowed_links"] if link["relation"] == relation][:2])
    return oracle


def _quality(root, data):
    comments = Counter()
    for path in sorted((root / "schema/tables").glob("*.yaml")):
        metadata = yaml.safe_load(path.read_text(encoding="utf-8"))
        for col in metadata["columns"]:
            text = str(col.get("column_comment") or "").strip()
            comments["empty" if not text else "echo" if text == col["column_name"] else "generic" if text in ("编码", "名称", "备注", "字段") else "useful"] += 1
    shapes = Counter()
    for records in data.values():
        for row in records:
            value = row["description"]
            shapes["empty" if not value else "json" if value.startswith("{") else "code" if value.startswith("CAT") else "english" if value.startswith("Imported") else "chinese"] += 1
    return {"column_comments": dict(sorted(comments.items())), "description_shapes": dict(sorted(shapes.items())),
            "source_types": dict(sorted(Counter(r["source_type"] for r in data[REFS]).items())),
            "member_levels": dict(sorted(Counter(r["level_no"] for t in (MEMBERS, ORG) for r in data[t]).items()))}


def _hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


def _evaluation_path(root, evaluation):
    return Path(evaluation) if evaluation is not None else root.with_name(root.name + "_evaluation")


def generate(output: Path, rows: int = 10000, seed: int = DEFAULT_SEED, evaluation: Path | None = None):
    """Create a fresh fixture and sibling oracle; refuse existing nonempty outputs."""
    root = Path(output)
    evaluation = _evaluation_path(root, evaluation)
    _counts(rows)
    r, e = root.resolve(), evaluation.resolve()
    if r == e or r in e.parents or e in r.parents:
        raise ValueError("evaluation directory must be outside and separate from the dataset")
    for path in (root, evaluation):
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise FileExistsError(f"Refusing to overwrite nonempty output: {path}")
    data, counts, hints, rng = _build(rows, seed)
    oracle = _oracle(data, hints)
    for folder in ("data", "schema/tables", "schema/constraints", "schema/foreign_keys"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    evaluation.mkdir(parents=True, exist_ok=True)
    useful = {"unit": "计量单位", "api_url": "访问路径", "period": "会计期间", "is_required": "是否必填"}
    for table in base.TABLES:
        fields = _fields(table)
        columns = []
        for order, field in enumerate(fields, 1):
            p = rng.random()
            comment = "" if p < .30 else field if p < .83 else rng.choice(("编码", "名称", "备注", "字段")) if p < .96 else useful.get(field, "沿用历史口径")
            columns.append({"column_name": field, "ordinal_position": order, "data_type": base._type(field),
                            "is_not_null": False, "default_value": None, "column_comment": comment})
        identity = {"schema": base.SCHEMA, "table_name": table.name}
        metadata = {**identity, "table_comment": rng.choice(("", table.name, "业务配置")),
                    "relkind": "r", "estimated_rows": counts[table.name], "total_size": None,
                    "data_size": None, "columns": columns}
        for folder, value in (("tables", metadata), ("constraints", {**identity, "constraints": []}),
                              ("foreign_keys", {**identity, "foreign_keys": []})):
            (root / "schema" / folder / f"{table.name}.yaml").write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
        with (root / "data" / f"{table.name}.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(data[table.name])
    manifest = {"synthetic": True, "generator": VERSION, "seed": seed, "schema": base.SCHEMA,
                "table_count": len(data), "row_count": rows, "counts": counts,
                "source": "Entirely generated fruit-domain examples; no real data"}
    (root / "synthetic_manifest.yaml").write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    oracle["input_sha256"] = _hashes(root)
    _dump(evaluation / "oracle.json", oracle)
    _dump(evaluation / "quality_report.json", _quality(root, data))
    report = validate(root, evaluation)
    _dump(evaluation / "validation.json", report)
    return report


def validate(root: Path, evaluation: Path | None = None):
    """Validate transport, actual reference witnesses and intentionally unresolved cases."""
    root = Path(root)
    evaluation = _evaluation_path(root, evaluation)
    manifest = yaml.safe_load((root / "synthetic_manifest.yaml").read_text(encoding="utf-8"))
    oracle = json.loads((evaluation / "oracle.json").read_text(encoding="utf-8"))
    if oracle["input_sha256"] != _hashes(root):
        raise ValueError("input changed since oracle generation")
    expected_tables = set(DEFAULT_COUNTS)
    data = {}
    for folder in ("tables", "constraints", "foreign_keys"):
        if {p.stem for p in (root / "schema" / folder).glob("*.yaml")} != expected_tables:
            raise ValueError(f"schema/{folder} does not contain the expected tables")
    if {p.stem for p in (root / "data").glob("*.csv")} != expected_tables:
        raise ValueError("data table set mismatch")
    for table in base.TABLES:
        name = table.name
        metadata = yaml.safe_load((root / "schema/tables" / f"{name}.yaml").read_text(encoding="utf-8"))
        fields = [col["column_name"] for col in metadata["columns"]]
        if fields != _fields(table):
            raise ValueError(f"unexpected schema: {name}")
        with (root / "data" / f"{name}.csv").open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != fields:
                raise ValueError(f"header mismatch: {name}")
            data[name] = list(reader)
        if len(data[name]) != manifest["counts"][name]:
            raise ValueError(f"row count mismatch: {name}")
        if any(None in row or any(v is None for v in row.values()) for row in data[name]):
            raise ValueError(f"malformed CSV row: {name}")
    if sum(map(len, data.values())) != manifest["row_count"]:
        raise ValueError("total row count mismatch")
    if "api_id" in data[CARD][0] or any("api_id" in r["card_config_json"] for r in data[CARD]):
        raise ValueError("Card must be an independent resource")

    def endpoint(item):
        row = data[item["table"]][item["row"] - 1]
        if {field: row[field] for field in item["fields"]} != item["values"]:
            raise ValueError("oracle endpoint does not match transformed input")
        return row

    for link in oracle["allowed_links"]:
        source, target = endpoint(link["source"]), endpoint(link["target"])
        value = _path(source[link["reference_field"]], link["reference_path"])
        if link["array_member"]:
            if isinstance(value, str):
                value = json.loads(value)
            if not isinstance(value, list) or link["reference_value"] not in value:
                raise ValueError("array witness mismatch")
            value = link["reference_value"]
        if value != link["reference_value"] or value != target[link["target_field"]]:
            raise ValueError("reference witness mismatch")
        if any(not source[s] or source[s] != target[t] for s, t in link["scope_fields"]):
            raise ValueError("scope witness mismatch")
        if any(source[k] != v for k, v in link["condition"].items()):
            raise ValueError("condition witness mismatch")
        matches = [row for row in data[link["target"]["table"]]
                   if row[link["target_field"]] == value
                   and all(source[s] == row[t] for s, t in link["scope_fields"])]
        if len(matches) != 1:
            raise ValueError("accepted endpoint is not uniquely scoped")
        if link["relation"] == "resource_owner" and BUSINESS_KINDS[source["business_type"]] != RESOURCE_KIND[link["target"]["table"]]:
            raise ValueError("resource kind mismatch")
        if link["relation"] == "member_parent" and (link["source"]["table"] != link["target"]["table"] or int(source["level_no"]) <= int(target["level_no"])):
            raise ValueError("invalid accepted hierarchy")
    for link in oracle["forbidden_links"]:
        source, target = endpoint(link["source"]), endpoint(link["target"])
        if link["reason"] == "business_type_conflict":
            if BUSINESS_KINDS[source["business_type"]] == RESOURCE_KIND[link["target"]["table"]]:
                raise ValueError("negative owner control has no type conflict")
        elif link["reason"] == "unproven_cross_catalog_identity":
            if link["source"]["table"] == link["target"]["table"] or source["parent_member_code"] != target["member_code"]:
                raise ValueError("negative parent control has no cross-catalog collision")
    for case in oracle["ambiguous_cases"]:
        endpoint(case["source"])
        for candidate in case.get("candidates", []):
            endpoint(candidate)
    for case in oracle["anomaly_ledger"]:
        endpoint(case["source"])
    return {"status": "passed", "generator": VERSION, "seed": manifest["seed"],
            "table_count": len(data), "row_count": sum(map(len, data.values())),
            "counts": {t: len(v) for t, v in data.items()},
            "allowed_links": len(oracle["allowed_links"]), "forbidden_links": len(oracle["forbidden_links"]),
            "ambiguous_cases": len(oracle["ambiguous_cases"]), "anomalies": len(oracle["anomaly_ledger"]),
            "quality": _quality(root, data), "scope": "Fixture consistency only; extraction quality has not been evaluated."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    gen = commands.add_parser("generate")
    gen.add_argument("--output", required=True, type=Path)
    gen.add_argument("--rows", type=int, default=10000)
    gen.add_argument("--seed", type=int, default=DEFAULT_SEED)
    gen.add_argument("--evaluation", type=Path)
    check = commands.add_parser("validate")
    check.add_argument("--input", required=True, type=Path)
    check.add_argument("--evaluation", type=Path)
    args = parser.parse_args()
    result = generate(args.output, args.rows, args.seed, args.evaluation) if args.command == "generate" else validate(args.input, args.evaluation)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
