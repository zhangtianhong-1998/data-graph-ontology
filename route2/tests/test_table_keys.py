"""Codes repeated per tenant resolve inside the tenant, without column-name maps."""
import duckdb

from ontology_r2.dimension_members import annotate_dimension_members, compile_dimension_members
from ontology_r2.quantity_registry import annotate_quantity_registries
from ontology_r2.table_keys import annotate_table_keys, key_scope
from test_group_incremental import PROFILE


class Tables:
    def __init__(self, specs):
        self.db = duckdb.connect(":memory:")
        self.snapshot_id = "keys"
        self.evidence = {}
        self.tables = {}
        for index, (name, columns, rows) in enumerate(specs):
            sql_name = f"src_{index}"
            fields = ", ".join(f'"{column}" VARCHAR' for column in columns)
            self.db.execute(f'CREATE TABLE "{sql_name}" (__r2_row BIGINT, {fields})')
            placeholders = ", ".join("?" for _ in range(len(columns) + 1))
            self.db.executemany(f'INSERT INTO "{sql_name}" VALUES ({placeholders})',
                                [(i, *row) for i, row in enumerate(rows, 1)])
            self.tables[name] = {"sql_name": sql_name, "column_names": columns, "pk": [],
                                 "rows": len(rows), "columns": [], "table_name": name.split(".")[-1]}

    def record_id(self, table, row):
        return f"{table}:row:r{row['__r2_row']}"

    def close(self):
        self.db.close()


def tenant_data():
    tenants = ("T1", "T2")
    return Tables([
        ("demo.measure_def", ["measure_id", "measure_code", "measure_name", "inference_type", "unit", "tenant_id"],
         [(str(i), code, name, "SUM", "元", tenant) for i, (code, name, tenant) in enumerate(
             [(c, n, t) for t in tenants for c, n in (("MES1", "收入"), ("MES2", "成本"))], 1)]),
        ("demo.dim_definition", ["dim_def_id", "dim_code", "dim_cn_name", "tenant_id"],
         [(str(i), code, name, tenant) for i, (code, name, tenant) in enumerate(
             [(c, n, t) for t in tenants for c, n in (("D1", "地区"), ("D2", "品类"))], 1)]),
        ("demo.dim_member", ["member_id", "dim_code", "member_code", "member_cn_name", "parent_member_code",
                             "tenant_id"],
         [(str(i), dim, code, name, parent, tenant) for i, (dim, code, name, parent, tenant) in enumerate(
             [(d, c, n, p, t) for t in tenants for d, c, n, p in (
                 ("D1", "ROOT", "全部地区", None), ("D1", "EAST", "华东", "ROOT"),
                 ("D2", "ROOT", "全部品类", None), ("D2", "APPLE", "苹果", "ROOT"))], 1)]),
        ("demo.metric_attr", ["attr_id", "measure_code", "tenant_id"],
         [(str(i), code, tenant) for i, (code, tenant) in enumerate(
             [(c, t) for t in tenants for c in ("MES1", "MES2")], 1)]),
    ])


def test_tenant_is_the_scope_and_codes_are_keys_inside_it():
    data = tenant_data()
    try:
        report = annotate_table_keys(data)
        assert report["scope_columns"] == ["tenant_id"]
        measure = data.tables["demo.measure_def"]["table_keys"]["business_key"]
        assert measure["columns"] == ["measure_code"] and measure["scope"] == ["tenant_id"]
        member = data.tables["demo.dim_member"]["table_keys"]["business_key"]
        assert member["columns"] == ["dim_code", "member_code"]
        assert key_scope(data.tables["demo.dim_member"], "member_code") == ["tenant_id", "dim_code"]
    finally:
        data.close()


def test_registry_and_member_detection_use_codes_unique_inside_the_tenant():
    data = tenant_data()
    try:
        annotate_table_keys(data)
        registry = annotate_quantity_registries(data)["tables"][0]
        assert registry["code_column"] == "measure_code"
        assert registry["scope_columns"] == ["tenant_id"]
        annotate_dimension_members(data)
        assert data.tables["demo.dim_member"]["member_table"]["scope_columns"] == ["tenant_id"]
        assert data.tables["demo.dim_definition"]["dimension_registry"]["code_column"] == "dim_code"
        ids = [item["id"] for item in compile_dimension_members(data)["members"]]
        assert "member:demo.dim_member:T1:D1:EAST" in ids and "member:demo.dim_member:T2:D1:EAST" in ids
    finally:
        data.close()


def test_registry_rows_become_types_and_links_resolve_inside_the_tenant():
    from ontology_r2.models import BuildPlan, DerivedType, TablePlan
    from ontology_r2.structural_relations import attach_structural_relations
    data = tenant_data()
    try:
        annotate_table_keys(data)
        annotate_quantity_registries(data)
        annotate_dimension_members(data)
        object_types, tables = [], []
        for name in data.tables:
            data.evidence["schema:" + name] = {"id": "schema:" + name, "origin": "declared_metadata",
                                               "raw_fragment": "", "source_ref": {"table": name}}
            for column in data.tables[name]["column_names"]:
                data.evidence[f"schema:{name}:{column}"] = {
                    "id": f"schema:{name}:{column}", "origin": "declared_metadata", "raw_fragment": "",
                    "source_ref": {"table": name, "column": column}}
            object_types.append(DerivedType(id="source_record_type:" + name, parent="GeneralObject",
                                            label=name, definition=name, category="source_record_type",
                                            evidence_ids=["schema:" + name]))
            tables.append(TablePlan(table=name, object_type="source_record_type:" + name,
                                    evidence_ids=["schema:" + name]))
        updated, report = attach_structural_relations(
            data, BuildPlan(object_types=object_types, tables=tables), PROFILE)
        assert report["errors"] == []
        labels = {item.id: item.label for item in updated.object_types if item.category == "business_type"}
        income_t1 = data._registry_row_types["demo.measure_def:row:r1"]
        income_t2 = data._registry_row_types["demo.measure_def:row:r3"]
        category_t1 = data._registry_row_types["demo.dim_definition:row:r2"]
        category_t2 = data._registry_row_types["demo.dim_definition:row:r4"]
        assert income_t1 == income_t2 and labels[income_t1] == "收入"
        assert category_t1 == category_t2 and labels[category_t1] == "品类"
        assert len(labels) == 4 and len(report["record_alignments"]) == 8
        assert data._dimension_row_types[("demo.dim_definition", (("tenant_id", "T1"),), "D2")] == category_t1
        assert data._dimension_row_types[("demo.dim_definition", (("tenant_id", "T2"),), "D2")] == category_t1
        assert data._registry_reference_scopes["demo.measure_def:row:r1"]["tenant_id"] == "T1"
        assert data._registry_reference_scopes["demo.measure_def:row:r3"]["tenant_id"] == "T2"
        assert not any(type_id.endswith(("demo.measure_def", "demo.dim_definition")) for type_id in labels)
        plans = {(item.source_table, item.source_column): item for item in updated.relations}
        attr = plans[("demo.metric_attr", "measure_code")]
        assert attr.scope_bindings == {"tenant_id": "tenant_id"}
        member = plans[("demo.dim_member", "dim_code")]
        assert member.scope_bindings == {"tenant_id": "tenant_id"}
        assert {item["mapping_kind"] for item in report["record_alignments"]} == {"exact"}
    finally:
        data.close()


def test_discovery_checks_a_tenant_scoped_code_inside_the_tenant():
    from ontology_r2.discovery import _bind_detected_scope
    data = tenant_data()
    try:
        annotate_table_keys(data)
        candidate = {"source": {"table": "demo.dim_member", "field": "dim_code"},
                     "target": {"table": "demo.dim_definition", "field": "dim_code"}}
        parent = {"source": {"table": "demo.dim_member", "field": "parent_member_code"},
                  "target": {"table": "demo.dim_member", "field": "member_code"}}
        _bind_detected_scope(data, [candidate, parent])
        assert candidate["suggested_scope_bindings"] == {"tenant_id": "tenant_id"}
        assert parent["suggested_scope_bindings"] == {"tenant_id": "tenant_id", "dim_code": "dim_code"}
    finally:
        data.close()


def test_numeric_key_counts_as_reference_only_under_the_type_id_convention():
    from types import SimpleNamespace
    from ontology_r2.group_incremental import _key_reference_evidence
    keys = {"scope_columns": ["tenant_id"], "keys": [
        {"columns": ["api_id"], "scope": ["tenant_id"], "kind": "surrogate", "max_chars": 2}]}
    data = SimpleNamespace(snapshot_id="s", evidence={}, tables={
        "demo.fruit_market_api": {"table_keys": keys}, "demo.output_param": {"table_keys": keys}})
    checks = {"eligible_references": 4, "unique_matches": 4, "ambiguous_matches": 0}
    typed = {"rule_id": "r1", "status": "checked_technical",
             "source": {"table": "demo.param", "field": "business_id"},
             "target": {"table": "demo.fruit_market_api", "field": "api_id"},
             "selector": {"business_type": "API"}, "scope_bindings": {"tenant_id": "tenant_id"},
             "verification": {"checks": checks}}
    assert _key_reference_evidence(data, typed) == "structural:r1"
    coincidence = {**typed, "rule_id": "r2", "source": {"table": "demo.rule", "field": "param_id"},
                   "target": {"table": "demo.output_param", "field": "api_id"}}
    assert _key_reference_evidence(data, coincidence) is None
    unscoped = {**typed, "rule_id": "r3", "scope_bindings": {}}
    assert _key_reference_evidence(data, unscoped) is None
