"""Shared definitions retain every local registration without duplicating concepts."""

import pytest

from ontology_r2.models import BuildPlan
from ontology_r2.registry_rows import compile_registry_rows
from test_table_keys import Tables


_COLUMNS = ["code", "name", "formula", "unit", "inference_type", "period", "caliber",
            "tenant_id", "business_id", "domain_code", "description"]


def _row(code, *, name="销售收入", tenant="T1", business="B1", domain="SALES", **attributes):
    return {"code": code, "name": name, "formula": "sales_amount", "unit": "元",
            "inference_type": "SUM", "period": "YTD", "caliber": "实际",
            "tenant_id": tenant, "business_id": business, "domain_code": domain,
            "description": "销售收入的累计金额", **attributes}


def _data(*specs):
    """Supply already detected registry roles; these tests exercise compilation only."""
    data = Tables([(table, _COLUMNS, [tuple(row.get(column) for column in _COLUMNS) for row in rows])
                   for table, role, rows in specs])
    for table, role, rows in specs:
        data.tables[table]["quantity_registry"] = {
            "role": role, "code_column": "code", "name_column": "name",
            "formula_column": "formula", "unit_column": "unit",
            "operator_column": "inference_type", "scope_columns": ["tenant_id"],
        }
    return data


def _compile(data):
    plan = BuildPlan()
    report = compile_registry_rows(data, plan)
    return plan, report


def test_same_measure_registered_by_two_tenants_is_one_shared_object():
    data = _data(("alpha.catalog", "Measure", [
        _row("LOCAL_A", tenant="T1"), _row("LOCAL_B", tenant="T2")]))
    try:
        plan, report = _compile(data)
        assert len(plan.object_types) == len(report["concepts"]) == 1
        assert len(report["record_alignments"]) == 2
        assert len(data._registry_row_types) == len(data._registry_record_to_object) == 2
        assert len(set(data._registry_row_types.values())) == 1
        assert len(set(data._registry_record_to_object.values())) == 1
        assert plan.object_types[0].identity_qualifiers == {}
        assert {scope["tenant_id"] for scope in plan.object_types[0].reference_scopes} == {"T1", "T2"}
    finally:
        data.close()


def test_reference_scopes_keep_joint_business_and_domain_assignments():
    data = _data(("alpha.catalog", "Measure", [
        _row("LOCAL_A", tenant="T1", business="B1", domain="SALES"),
        _row("LOCAL_B", tenant="T1", business="B2", domain="SERVICE")]))
    try:
        plan, report = _compile(data)
        assert len(plan.object_types) == 1
        scopes = plan.object_types[0].reference_scopes
        assert len(scopes) == 2
        assert {(scope["source_table"], scope["tenant_id"], scope["business_id"], scope["domain_code"])
                for scope in scopes} == {
                    ("alpha.catalog", "T1", "B1", "SALES"),
                    ("alpha.catalog", "T1", "B2", "SERVICE"),
                }
        assert report["concepts"][0]["reference_scopes"] == scopes
    finally:
        data.close()


def test_merging_keeps_all_record_alignments_and_property_evidence():
    data = _data(("alpha.catalog", "Measure", [
        _row("A", tenant="T1"), _row("B", tenant="T2"), _row("C", tenant="T3")]))
    try:
        plan, report = _compile(data)
        assert len(plan.object_types) == len(report["concepts"]) == 1
        concept = report["concepts"][0]
        records = {f"alpha.catalog:row:r{index}" for index in range(1, 4)}
        assert {item["record_id"] for item in concept["source_refs"]} == records
        assert {item["source_record_id"] for item in report["record_alignments"]} == records
        assert {item["concept_id"] for item in report["record_alignments"]} == {concept["id"]}
        assert set(data._registry_row_types) == set(data._registry_record_to_object) == records
        for properties in (plan.object_types[0].source_properties, concept["source_properties"]):
            evidence_ids = {eid for item in properties
                            for eid in (item.evidence_ids if hasattr(item, "evidence_ids")
                                        else item["evidence_ids"])}
            assert {data.evidence[eid]["source_ref"]["record_id"] for eid in evidence_ids} == records
        assert set(plan.object_types[0].evidence_ids) == set(concept["evidence_ids"])
    finally:
        data.close()


def test_semantic_ids_ignore_row_order_local_codes_tenants_and_source_table():
    datasets = [
        _data(("alpha.first", "Measure", [_row("A", tenant="T1"), _row("B", name="销量", unit="吨")])),
        _data(("beta.second", "Measure", [_row("X", name="销量", tenant="OTHER", unit="吨"),
                                             _row("Y", tenant="OTHER")])),
    ]
    try:
        plans = [_compile(data)[0] for data in datasets]
        assert {item.label: item.id for item in plans[0].object_types} == {
            item.label: item.id for item in plans[1].object_types}
    finally:
        for data in datasets:
            data.close()


def test_equivalent_definitions_in_different_registry_tables_share_identity():
    data = _data(("alpha.first", "Measure", [_row("A", tenant="T1")]),
                 ("beta.second", "Measure", [_row("B", tenant="T9")]))
    try:
        plan, report = _compile(data)
        assert len(plan.object_types) == len(report["concepts"]) == 1
        assert len(report["record_alignments"]) == 2
        assert {scope["source_table"] for scope in plan.object_types[0].reference_scopes} == {
            "alpha.first", "beta.second"}
        assert {item.source_table for item in plan.object_types[0].source_properties} == {
            "alpha.first", "beta.second"}
    finally:
        data.close()


@pytest.mark.parametrize("attribute,value", [
    ("formula", "net_sales_amount"), ("unit", "美元"), ("period", "MTD"), ("caliber", "预算"),
])
def test_same_name_with_conflicting_definition_attributes_is_not_merged(attribute, value):
    data = _data(("alpha.catalog", "Measure", [_row("A"), _row("B", **{attribute: value})]))
    try:
        plan, report = _compile(data)
        assert len(plan.object_types) == len(report["concepts"]) == 2
        assert len(set(data._registry_row_types.values())) == 2
        assert len(set(data._registry_record_to_object.values())) == 2
    finally:
        data.close()


def test_missing_formula_cannot_bridge_two_known_conflicting_definitions():
    data = _data(("alpha.catalog", "Measure", [
        _row("A", formula="gross_sales"), _row("B", formula=None), _row("C", formula="net_sales")]))
    try:
        _compile(data)
        assert data._registry_row_types["alpha.catalog:row:r1"] != data._registry_row_types["alpha.catalog:row:r3"]
        assert data._registry_record_to_object["alpha.catalog:row:r1"] != data._registry_record_to_object["alpha.catalog:row:r3"]
        assert len(data._registry_row_types) == 3
    finally:
        data.close()


def test_same_formula_code_in_two_tenants_resolves_before_metric_identity():
    data = _data(
        ("alpha.measures", "Measure", [
            _row("M1", tenant="T1", formula="gross_sales"),
            _row("M1", tenant="T2", formula="net_sales")]),
        ("alpha.metrics", "Metric", [
            _row("K1", tenant="T1", formula="M1"), _row("K1", tenant="T2", formula="M1")]))
    try:
        plan, _ = _compile(data)
        assert len([item for item in plan.object_types if item.parent == "Metric"]) == 2
        assert data._registry_row_types["alpha.metrics:row:r1"] != data._registry_row_types["alpha.metrics:row:r2"]
    finally:
        data.close()


def test_different_local_formula_codes_for_one_measure_share_metric_identity():
    data = _data(
        ("alpha.measures", "Measure", [_row("M1", tenant="T1"), _row("M9", tenant="T2")]),
        ("alpha.metrics", "Metric", [
            _row("K1", tenant="T1", formula="M1"), _row("K2", tenant="T2", formula="M9")]))
    try:
        plan, report = _compile(data)
        assert len([item for item in plan.object_types if item.parent == "Measure"]) == 1
        assert len([item for item in plan.object_types if item.parent == "Metric"]) == 1
        assert len(report["record_alignments"]) == 4
        assert data._registry_row_types["alpha.metrics:row:r1"] == data._registry_row_types["alpha.metrics:row:r2"]
        assert data._registry_record_to_object["alpha.metrics:row:r1"] == data._registry_record_to_object["alpha.metrics:row:r2"]
    finally:
        data.close()


def test_opaque_formulas_preserve_whitespace_inside_quoted_literals():
    data = _data(("alpha.catalog", "Measure", [
        _row("A", formula="CASE WHEN region = 'A  B' THEN amount END"),
        _row("B", formula="CASE WHEN region = 'A B' THEN amount END")]))
    try:
        plan, _ = _compile(data)
        assert len(plan.object_types) == 2
        assert data._registry_row_types["alpha.catalog:row:r1"] != data._registry_row_types["alpha.catalog:row:r2"]
    finally:
        data.close()


def test_blank_dedicated_description_does_not_hide_conflicting_business_definitions():
    data = _data(("alpha.catalog", "Measure", [
        _row("A", description="期末余额口径，按最后一天余额取值"),
        _row("B", description="期间发生额口径，按所有流水求和")]))
    try:
        data.db.execute('ALTER TABLE "src_0" ADD COLUMN measure_description VARCHAR')
        data.tables["alpha.catalog"]["column_names"].append("measure_description")
        plan, _ = _compile(data)
        assert len(plan.object_types) == 2
        assert {item.definition for item in plan.object_types} == {
            "期末余额口径，按最后一天余额取值", "期间发生额口径，按所有流水求和"}
    finally:
        data.close()


def test_opaque_formula_cannot_hide_different_scoped_measure_dependencies():
    data = _data(
        ("alpha.measures", "Measure", [
            _row("M1", tenant="T1", formula="gross_sales"),
            _row("M1", tenant="T2", formula="net_sales")]),
        ("alpha.metrics", "Metric", [
            _row("K1", tenant="T1", formula="M1 WHERE status = 'active'"),
            _row("K1", tenant="T2", formula="M1 WHERE status = 'active'")]))
    try:
        plan, _ = _compile(data)
        assert len([item for item in plan.object_types if item.parent == "Metric"]) == 2
        assert data._registry_row_types["alpha.metrics:row:r1"] != data._registry_row_types["alpha.metrics:row:r2"]
    finally:
        data.close()
