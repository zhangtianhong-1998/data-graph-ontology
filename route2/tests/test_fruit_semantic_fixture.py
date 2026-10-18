"""Check synthetic source consistency without asserting model-produced ontology."""
import csv
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import generate_fruit_semantic_data as fixture


def test_coherent_source_has_real_formula_endpoints_and_instance_bindings(tmp_path):
    root = tmp_path / 'coherent'
    result = fixture.generate(root, scale=.001)
    assert result['table_count'] == 23
    assert result['distinct_quantity_symbols'] == len(fixture.QUANTITIES)
    assert result['formulas_with_resolved_symbols'] > 0
    with (root / 'data/fruit_measure_def.csv').open(newline='') as f:
        quantities = list(csv.DictReader(f))
    rate = next(r for r in quantities if r['measure_name'] == '合格率')
    assert 'tested_quantity' in rate['source_field']
    assert rate['standard_name'] == '合格率'
    assert '苹果' not in rate['measure_description']
    with (root / 'data/fruit_metric_detail.csv').open(newline='') as f:
        metrics = list(csv.DictReader(f))
    assert any(r['metric_name'] == '水果合格率' for r in metrics)
    assert any('配置对象' in r['metric_definition'] for r in metrics)
    with (root / 'data/fruit_dashboard_card.csv').open(newline='') as f:
        assert all('具体看板实例' in r['card_description'] for r in csv.DictReader(f))


def test_stripped_fruit_keeps_measure_metric_and_member_relations(tmp_path):
    from ontology_r2.dimension_members import annotate_dimension_members, compile_dimension_members
    from ontology_r2.models import BuildPlan, DerivedType, TablePlan
    from ontology_r2.quantity_registry import annotate_quantity_registries
    from ontology_r2.storage import Dataset
    from ontology_r2.structural_relations import attach_structural_relations
    from test_group_incremental import PROFILE

    root = tmp_path / "stripped"
    fixture.generate(root, scale=.001, strip_classification=True)
    with (root / "data/fruit_measure_def.csv").open(newline="", encoding="utf-8") as handle:
        assert all("可复用度量定义" not in row["measure_description"] for row in csv.DictReader(handle))
    work = tmp_path / "work"
    work.mkdir()
    data = Dataset(root, work)
    try:
        roles = {item["table"]: item["role"] for item in annotate_quantity_registries(data)["tables"]}
        assert roles["fruit_market.fruit_measure_def"] == "Measure"
        assert roles["fruit_market.fruit_metric_detail"] == "Metric"
        annotate_dimension_members(data)
        member = data.tables["fruit_market.fruit_dim_member"]["member_table"]
        dimension = data.tables["fruit_market.fruit_dim_definition"]["dimension_registry"]
        assert member["code_column"] == "member_code"
        assert dimension["code_column"] == "dim_code"
        assert "dimension_registry" not in data.tables["fruit_market.fruit_dashboard_card"]
        compiled = compile_dimension_members(data)
        assert compiled["coverage"]["member_rows"] > 0
        object_types, tables = [], []
        for name in data.tables:
            evidence_id = "schema:" + name
            type_id = "source_record_type:" + name
            object_types.append(DerivedType(
                id=type_id, parent="GeneralObject", label=name, definition=name,
                evidence_ids=[evidence_id], category="source_record_type"))
            tables.append(TablePlan(table=name, object_type=type_id, evidence_ids=[evidence_id]))
        updated, report = attach_structural_relations(
            data, BuildPlan(object_types=object_types, tables=tables), PROFILE)
        assert report["errors"] == []
        assert report["attached"] > 0
        assert any(item.source_table == "fruit_market.fruit_dim_member" and item.source_column == "dim_code"
                   for item in updated.relations)
        assert {item["type"] for item in report["concepts"]} >= {"Measure", "Metric", "Dimension"}
        assert any(item["kind"] == "code_name" for item in report["column_links"])
    finally:
        data.close()


def test_unbound_formula_is_detected_not_repaired_by_common_sense(tmp_path):
    root = tmp_path / 'coherent'
    fixture.generate(root, scale=.001)
    path = root / 'data/fruit_metric_detail.csv'
    with path.open(newline='') as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames, list(reader)
    rows[0]['calculation_formula'] = 'x = absent_quantity + sales_volume'
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fields)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match='Unbound formula symbols'):
        fixture.validate_semantics(root)


def test_v2_remains_the_legacy_input_not_silently_repaired():
    counts = fixture.base.scaled_counts(1.0)
    table = fixture.base.TABLE_BY_NAME['fruit_measure_def']
    legacy = fixture.base.make_row(table, 30, counts)
    assert legacy['standard_name'] == '苹果合格率'
    assert legacy['source_field'] == 'avg_price_cny_per_kg'
    assert sum(counts.values()) == 1_102_238
