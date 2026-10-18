"""Definition tables can carry Measure/Metric without a genericity sentence."""
import duckdb
import pytest

from ontology_r2.group_incremental import ConceptBundleDecision, compile_concept
from ontology_r2.quantity_registry import annotate_quantity_registries
from test_group_incremental import PROFILE


class Tables:
    def __init__(self, specs):
        self.db = duckdb.connect(":memory:")
        self.snapshot_id = "registry-snapshot"
        self.evidence = {}
        self.tables = {}
        for index, (name, columns, rows) in enumerate(specs):
            sql_name = f"src_{index}"
            fields = ", ".join(f'"{column}" VARCHAR' for column in columns)
            self.db.execute(f'CREATE TABLE "{sql_name}" (__r2_row BIGINT, {fields})')
            placeholders = ", ".join("?" for _ in range(len(columns) + 1))
            self.db.executemany(
                f'INSERT INTO "{sql_name}" VALUES ({placeholders})',
                [(i, *row) for i, row in enumerate(rows, 1)])
            self.tables[name] = {"sql_name": sql_name, "column_names": columns,
                                 "rows": len(rows), "columns": []}

    def record_id(self, table, row):
        return f"{table}:row:r{row['__r2_row']}"

    def close(self):
        self.db.close()


def test_shape_separates_measure_metric_and_product_tables():
    data = Tables([
        ("demo.measures", ["measure_code", "measure_name", "inference_type", "unit"], [
            ("M1", "收入", "SUM", "元"), ("M2", "成本", "SUM", "元"),
        ]),
        ("demo.metrics", ["metric_code", "metric_name", "calculation_formula"], [
            ("K1", "销售总额", "sales = volume * price"),
            ("K2", "出口增长", "growth = current - previous"),
        ]),
        ("demo.products", ["product_code", "product_name", "unit"], [
            ("P1", "苹果", "箱"), ("P2", "香蕉", "箱"),
        ]),
    ])
    try:
        report = annotate_quantity_registries(data)
        roles = {item["table"]: item["role"] for item in report["tables"]}
        assert roles == {"demo.measures": "Measure", "demo.metrics": "Metric"}
        assert "quantity_registry" not in data.tables["demo.products"]
    finally:
        data.close()


def _decision(record_id, label, quote, *, root="Measure", basis="schema_typed"):
    return ConceptBundleDecision(
        status="proposed", label=label, definition=quote, root_type=root,
        ontology_level="type", classification_basis=basis, classification_quote=quote,
        alignments=[{"record_id": record_id, "mapping_kind": "exact", "quote": quote}])


def test_formula_table_stays_metric_when_an_aggregation_column_is_added():
    data = Tables([
        ("demo.indicators", ["code", "name", "calculation_formula", "aggregation"], [
            ("K1", "销售总额", "sales = volume * price", "SUM"),
            ("K2", "出口增长", "growth = current - previous", "SUM"),
        ]),
    ])
    try:
        report = annotate_quantity_registries(data)
        assert {item["table"]: item["role"] for item in report["tables"]} == {
            "demo.indicators": "Metric"}
    finally:
        data.close()


def test_aggregation_column_does_not_relabel_a_metric_table():
    data = Tables([
        ("demo.metrics", ["metric_code", "metric_name", "calculation_formula", "aggregation"], [
            ("K1", "销售总额", "sales = volume * price", "SUM"),
            ("K2", "出口增长", "growth = current - previous", "SUM"),
        ]),
    ])
    try:
        report = annotate_quantity_registries(data)
        assert report["tables"][0]["role"] == "Metric"
    finally:
        data.close()


def test_stripped_measure_text_still_uses_the_registry():
    import sys
    from pathlib import Path
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    import generate_fruit_semantic_data as fixture
    row = {"measure_description": "收入按成交金额求和。这是可复用度量定义，不限定具体经营对象、产品、地区或实际期间。"}
    cleaned = fixture.strip_classification_cues(row)
    assert "可复用度量定义" not in cleaned["measure_description"]
    assert "收入按成交金额求和" in cleaned["measure_description"]
    data = Tables([
        ("demo.measures", ["measure_code", "measure_name", "inference_type"], [
            ("M1", "收入", "SUM"), ("M2", "成本", "SUM"),
        ]),
    ])
    try:
        annotate_quantity_registries(data)
        definition = "收入按成交金额求和。"
        record = {"record_id": "r1", "table": "demo.measures", "row_number": 1,
                  "kind": "definition", "scope": {}, "unit": "元", "fields": {
                      "name": [{"column": "measure_name", "value": "收入"}],
                      "description": [{"column": "measure_description", "value": definition}],
                      "unit": [{"column": "unit", "value": "元"}],
                  }}
        bundle = {"records": [record], "exact_alignment_record_ids": ["r1"]}
        concept, _ = compile_concept(data, PROFILE, bundle, _decision("r1", "收入", definition), {})
        assert concept["type"] == "Measure"
        proof = concept["classification_evidence"]["measure_reuse_assessment"][0]
        assert proof["supported"] and proof["proofs"][0]["basis"] == "quantity_registry"
        reused = _decision("r1", "收入", definition, basis="reusable_measure")
        assert compile_concept(data, PROFILE, bundle, reused, {})[0]["type"] == "Measure"
    finally:
        data.close()


def test_schema_typed_without_a_registry_is_rejected():
    data = Tables([
        ("demo.products", ["product_code", "product_name", "unit"], [
            ("P1", "苹果", "箱"), ("P2", "香蕉", "箱"),
        ]),
    ])
    try:
        annotate_quantity_registries(data)
        definition = "苹果按箱计数。"
        record = {"record_id": "r1", "table": "demo.products", "row_number": 1,
                  "kind": "definition", "scope": {}, "fields": {
                      "name": [{"column": "product_name", "value": "苹果"}],
                      "description": [{"column": "description", "value": definition}],
                  }}
        bundle = {"records": [record], "exact_alignment_record_ids": ["r1"]}
        with pytest.raises(ValueError, match="matching classification basis"):
            compile_concept(data, PROFILE, bundle, _decision("r1", "苹果", definition), {})
    finally:
        data.close()


def test_registry_does_not_let_a_short_name_replace_its_standard_name():
    data = Tables([
        ("demo.measures", ["measure_code", "measure_name", "inference_type"], [
            ("M1", "均价", "AVG"), ("M2", "成本", "SUM"),
        ]),
    ])
    try:
        annotate_quantity_registries(data)
        definition = "芒果均价，按非洲和会计期汇总。"
        record = {"record_id": "r1", "table": "demo.measures", "row_number": 1,
                  "kind": "definition", "scope": {}, "fields": {
                      "name": [{"column": "measure_name", "value": "均价"},
                               {"column": "standard_name", "value": "芒果均价"}],
                      "description": [{"column": "measure_description", "value": definition}],
                  }}
        bundle = {"records": [record], "exact_alignment_record_ids": ["r1"]}
        with pytest.raises(ValueError, match="short_name_does_not_replace_standard_name"):
            compile_concept(data, PROFILE, bundle, _decision("r1", "均价", definition), {})
    finally:
        data.close()


def test_metric_registry_does_not_require_a_business_object_sentence():
    data = Tables([
        ("demo.metrics", ["metric_code", "metric_name", "calculation_formula"], [
            ("K1", "销售总额", "sales = volume * price"),
            ("K2", "出口增长", "growth = current - previous"),
        ]),
    ])
    try:
        annotate_quantity_registries(data)
        record = {"record_id": "r1", "table": "demo.metrics", "row_number": 1,
                  "kind": "definition", "scope": {}, "fields": {
                      "name": [{"column": "metric_name", "value": "销售总额"}],
                      "formula": [{"column": "calculation_formula", "value": "sales = volume * price"}],
                  }}
        bundle = {"records": [record], "exact_alignment_record_ids": ["r1"]}
        decision = _decision("r1", "销售总额", "sales = volume * price", root="Metric")
        decision.classification_quote = ""
        concept, _ = compile_concept(data, PROFILE, bundle, decision, {})
        assert concept["type"] == "Metric"
        assert concept["classification_evidence"]["basis"] == "schema_typed"
    finally:
        data.close()
