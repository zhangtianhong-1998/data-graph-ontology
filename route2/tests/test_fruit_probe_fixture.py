"""Test the small source fixture and its oracle, not extractor predictions."""
import csv
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
import yaml


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/generate_fruit_probe_data.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("generate_fruit_probe_data", SCRIPT)
fixture = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fixture
SPEC.loader.exec_module(fixture)


def _fingerprint(root, pattern="**/*"):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.glob(pattern)) if path.is_file()
    }


def _csv_rows(root):
    tables = {}
    for path in sorted((root / "data").glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as stream:
            tables[path.stem] = list(csv.DictReader(stream))
    return tables


@pytest.fixture(scope="module")
def small_probe(tmp_path_factory):
    output = tmp_path_factory.mktemp("fruit-probe") / "input"
    result = fixture.generate(output, rows=3000, seed=20261016)
    evaluation = output.with_name(output.name + "_evaluation")
    oracle = json.loads((evaluation / "oracle.json").read_text(encoding="utf-8"))
    return output, evaluation, result, oracle


def test_requested_small_size_loads_through_real_dataset(small_probe, tmp_path):
    from ontology_r2.storage import Dataset

    root, evaluation, _, _ = small_probe
    source_rows = _csv_rows(root)
    assert len(source_rows) == 23
    assert sum(map(len, source_rows.values())) == 3000
    work = tmp_path / "loader"
    work.mkdir()
    data = Dataset(root, work)
    try:
        assert len(data.tables) == len(source_rows)
        assert sum(table["rows"] for table in data.tables.values()) == 3000
        for table in data.tables.values():
            assert table["rows"] == len(source_rows[table["table_name"]])
            assert table["foreign_keys"] == []
        assert all(not (root / name).resolve().is_relative_to(evaluation.resolve())
                   for name in data.files)
    finally:
        data.close()


def test_seed_reproduces_input_and_external_truth(small_probe, tmp_path):
    root, evaluation, _, _ = small_probe
    repeated, changed = tmp_path / "repeated", tmp_path / "changed"
    fixture.generate(repeated, rows=3000, seed=20261016)
    fixture.generate(changed, rows=3000, seed=20261017)
    assert _fingerprint(root) == _fingerprint(repeated)
    assert _fingerprint(evaluation) == _fingerprint(
        repeated.with_name(repeated.name + "_evaluation"))
    assert _fingerprint(root, "data/*.csv") != _fingerprint(changed, "data/*.csv")


def test_oracle_is_not_an_input_and_old_classification_answers_are_absent(small_probe):
    root, evaluation, _, oracle = small_probe
    assert not evaluation.resolve().is_relative_to(root.resolve())
    assert oracle["expected_table_roles"]
    assert not list(root.rglob("*oracle*"))
    forbidden = (
        "这是可复用度量定义", "这是业务驱动指标定义", "Metric/Measure",
        "classification_basis", "expected_table_roles", "allowed_links",
        "本记录是一个具体看板实例", "API是一类对外提供数据查询的服务资源",
    )
    for path in root.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert not any(answer in text for answer in forbidden), path
    comments = []
    for path in (root / "schema/tables").glob("*.yaml"):
        schema = yaml.safe_load(path.read_text(encoding="utf-8"))
        comments.extend((column["column_name"], column.get("column_comment") or "")
                        for column in schema["columns"])
    uninformative = sum(not comment.strip() or comment.strip() == name
                        for name, comment in comments)
    assert uninformative > len(comments) / 2


def _row(tables, endpoint):
    assert endpoint["row"] >= 1
    row = tables[endpoint["table"]][endpoint["row"] - 1]
    assert endpoint["values"] == {field: row[field] for field in endpoint["fields"]}
    return row


def _identity(endpoint):
    return endpoint["table"], endpoint["row"]


def test_control_links_have_real_scoped_witnesses_and_no_card_api_assumption(small_probe):
    root, _, _, oracle = small_probe
    tables = _csv_rows(root)
    assert oracle["controls"]
    assert {link["relation"] for link in oracle["controls"]} == {
        link["relation"] for link in oracle["allowed_links"]}
    assert all(link in oracle["allowed_links"] for link in oracle["controls"])
    business_kinds = {"API": "fruit_market_api", "api": "fruit_market_api", "接口": "fruit_market_api",
                      "CARD": "fruit_dashboard_card", "card": "fruit_dashboard_card", "卡片": "fruit_dashboard_card"}
    for link in oracle["allowed_links"]:
        source, target = _row(tables, link["source"]), _row(tables, link["target"])
        value = source[link["reference_field"]]
        if link["reference_path"] or link["array_member"]:
            value = json.loads(value)
            for part in link["reference_path"]:
                value = value[part]
        if link["array_member"]:
            assert link["reference_value"] in value
            value = link["reference_value"]
        assert value == target[link["target_field"]] == link["reference_value"]
        assert link["expected_status"] == "accepted"
        assert all(source[s] and source[s] == target[t] for s, t in link["scope_fields"])
        assert all(source[k] == v for k, v in link["condition"].items())
        if link["relation"] == "resource_owner":
            assert link["target"]["table"] == business_kinds[source["business_type"]]
        assert {link["source"]["table"], link["target"]["table"]} != {
            "fruit_market_api", "fruit_dashboard_card"}
    assert "api_id" not in tables["fruit_dashboard_card"][0]


def test_hierarchy_collisions_are_scoped_and_wrong_alternatives_are_rejected(small_probe):
    root, _, _, oracle = small_probe
    tables = _csv_rows(root)
    parents = [link for link in oracle["allowed_links"] if link["relation"] == "member_parent"]
    assert parents
    for link in parents:
        source, target = _row(tables, link["source"]), _row(tables, link["target"])
        assert link["source"]["table"] == link["target"]["table"]
        assert (source["tenant_id"], source["dim_code"]) == (target["tenant_id"], target["dim_code"])
        assert source["parent_member_code"] == target["member_code"]
        assert int(source["level_no"]) > int(target["level_no"])
    rejected = [link for link in oracle["forbidden_links"] if link["relation"] == "member_parent"]
    assert rejected
    parent_sources = {_identity(link["source"]) for link in parents}
    for link in rejected:
        source, target = _row(tables, link["source"]), _row(tables, link["target"])
        assert link["expected_status"] == "rejected"
        assert source["parent_member_code"] == target["member_code"]
        assert link["source"]["table"] != link["target"]["table"]
        assert _identity(link["source"]) in parent_sources
    all_members = [(name, row) for name in ("fruit_dim_member", "fruit_org_dim_data") for row in tables[name]]
    assert {row["level_no"] for _, row in all_members} >= {"0", "1", "2"}
    assert len({row["member_code"] for _, row in all_members}) < len(all_members) / 2


def test_intentional_defects_remain_distinct_from_valid_controls(small_probe):
    root, _, _, oracle = small_probe
    tables = _csv_rows(root)
    anomalies = oracle["anomaly_ledger"]
    assert {item["kind"] for item in anomalies} >= {
        "malformed_json", "self_parent_cycle", "missing_display_name", "dangling_parameter"}
    accepted_parents = {_identity(link["source"]) for link in oracle["allowed_links"]
                        if link["relation"] == "member_parent"}
    accepted_parameters = {_identity(link["source"]) for link in oracle["allowed_links"]
                           if link["relation"] == "rule_parameter"}
    for item in anomalies:
        row = _row(tables, item["source"])
        if item["kind"] == "malformed_json":
            with pytest.raises(json.JSONDecodeError):
                json.loads(row["field_rule"])
        elif item["kind"] == "self_parent_cycle":
            assert row["parent_member_code"] == row["member_code"]
            assert _identity(item["source"]) not in accepted_parents
        elif item["kind"] == "missing_display_name":
            assert not row["member_cn_name"] and row["member_code"]
        elif item["kind"] == "dangling_parameter":
            assert _identity(item["source"]) not in accepted_parameters
    assert oracle["ambiguous_cases"]
    for item in oracle["ambiguous_cases"]:
        _row(tables, item["source"])
        assert item["expected_status"] == "unresolved"
        for candidate in item["candidates"]:
            _row(tables, candidate)
    assert any(len(item["candidates"]) > 1 for item in oracle["ambiguous_cases"])
    partial_ranges = {
        _identity(item["source"]) for item in anomalies
        if item["kind"] == "dangling_reference" and "field_rule" in item["source"]["fields"]}
    valid_ranges = {
        _identity(link["source"]) for link in oracle["allowed_links"]
        if link["reference_field"] == "field_rule"}
    assert partial_ranges & valid_ranges, "Mixed valid/dangling arrays need a partial-success control"


def test_quantity_control_formulas_have_consistent_units_and_periods(small_probe):
    root, _, _, _ = small_probe
    tables = _csv_rows(root)
    measures = {(row["tenant_id"], row["measure_code"]): row
                for row in tables["fruit_measure_def"]}
    metrics = {(row["tenant_id"], row["code"]): row
               for row in tables["fruit_metric_detail"]}
    formula_kinds = set()
    for row in metrics.values():
        formula = row["calculation_formula"]
        symbols = re.findall(r"MES\d+", formula)
        if not symbols:
            # Prose-only definitions are deliberate missing formula evidence.
            continue
        operands = [measures[(row["tenant_id"], symbol)] for symbol in symbols]
        if len(operands) == 1:
            assert formula == operands[0]["measure_code"]
            assert row["unit"] == operands[0]["unit"]
            formula_kinds.add("direct")
        else:
            assert len(operands) == 2 and "/" in formula
            assert (operands[0]["unit"], operands[1]["unit"], row["unit"]) == ("元", "吨", "元/吨")
            formula_kinds.add("ratio")
    assert formula_kinds == {"direct", "ratio"}
    for row in measures.values():
        standard = row["standard_name"]
        if "YTD" in standard:
            assert row["period"] == "YTD"
        elif "MTD" in standard:
            assert row["period"] == "MTD"
        elif "(Q)" in standard:
            assert row["period"] == "Q"
        elif "(H1)" in standard:
            assert row["period"] == "H1"
        if standard.startswith("Budget"):
            assert row["budget_flag"] == "1" and row["actual_flag"] == "0"
        if "Forecast" in standard:
            assert row["forecast_flag"] == "1"
    for row in tables["fruit_metric_attr"]:
        metric = metrics[(row["tenant_id"], row["metric_code"])]
        measure = measures[(row["tenant_id"], row["measure_code"])]
        assert row["unit"] == metric["unit"] == measure["unit"]
        assert row["metric_formula"] == metric["calculation_formula"]


def test_generation_refuses_to_replace_existing_input(small_probe):
    root, evaluation, _, _ = small_probe
    before = _fingerprint(root), _fingerprint(evaluation)
    with pytest.raises(FileExistsError):
        fixture.generate(root, rows=3000, seed=1)
    assert (_fingerprint(root), _fingerprint(evaluation)) == before


def test_existing_evaluation_is_preserved_before_new_input_is_created(tmp_path):
    root, evaluation = tmp_path / "input", tmp_path / "input_evaluation"
    evaluation.mkdir()
    sentinel = evaluation / "review.txt"
    sentinel.write_text("existing experiment", encoding="utf-8")
    with pytest.raises(FileExistsError):
        fixture.generate(root, rows=3000)
    assert sentinel.read_text(encoding="utf-8") == "existing experiment"
    assert not root.exists()


def test_evaluation_inside_input_is_rejected_before_writing(tmp_path):
    root = tmp_path / "input"
    with pytest.raises(ValueError):
        fixture.generate(root, rows=3000, evaluation=root / "evaluation")
    assert not root.exists()


@pytest.mark.parametrize("rows", [0, 1999])
def test_too_small_request_is_rejected_before_writing(tmp_path, rows):
    root = tmp_path / "invalid"
    with pytest.raises(ValueError):
        fixture.generate(root, rows=rows)
    assert not root.exists()
