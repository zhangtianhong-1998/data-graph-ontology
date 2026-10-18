"""Member rows stay instances under a dimension, including definition-shaped cards."""
import duckdb
import pytest

from ontology_r2.dimension_members import annotate_dimension_members, compile_dimension_members
from ontology_r2.group_incremental import ConceptBundleDecision, compile_concept
from test_group_incremental import PROFILE


class Tables:
    def __init__(self, specs):
        self.db = duckdb.connect(":memory:")
        self.snapshot_id = "members"
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


def _bundle(table, label, definition):
    record = {"record_id": "r1", "table": table, "row_number": 1, "kind": "definition",
              "scope": {}, "fields": {
                  "name": [{"column": "member_cn_name", "value": label}],
                  "description": [{"column": "note", "value": definition}],
              }}
    decision = ConceptBundleDecision(
        status="proposed", label=label, definition=definition, root_type="Term",
        ontology_level="type", classification_basis="other", classification_quote=definition,
        alignments=[{"record_id": "r1", "mapping_kind": "exact", "quote": definition}])
    return {"records": [record], "exact_alignment_record_ids": ["r1"]}, decision


def test_member_and_alias_tables_are_not_types():
    data = Tables([
        ("demo.dimensions", ["dim_code", "dim_cn_name"], [("D1", "水果类别"), ("D2", "产区")]),
        ("demo.members", ["dim_code", "member_code", "member_cn_name", "member_en_name",
                          "parent_member_code", "level_no"], [
            ("D1", "APPLE", "苹果", "apple", "D1", "2"),
            ("D1", "BANANA", "香蕉", "banana", "D1", "2"),
            ("D2", "EAST", "华东", "east", "D2", "2"),
        ]),
        ("demo.aliases", ["word", "attr"], [
            ("banana", '{"fruit_code":"BANANA"}'),
            ("橙子", '{"fruit_code":"APPLE"}'),
        ]),
        ("demo.products", ["product_code", "product_name"], [("P1", "苹果"), ("P2", "香蕉")]),
    ])
    try:
        report = annotate_dimension_members(data)
        assert [item["table"] for item in report["member_tables"]] == ["demo.members"]
        assert [item["table"] for item in report["dimension_tables"]] == ["demo.dimensions"]
        assert [item["table"] for item in report["alias_tables"]] == ["demo.aliases"]
        assert "member_table" not in data.tables["demo.products"]
        compiled = compile_dimension_members(data)
        by_code = {item["member_code"]: item for item in compiled["members"]}
        assert by_code["APPLE"]["id"] == "member:demo.members:D1:APPLE"
        assert by_code["APPLE"]["names"] == ["苹果", "apple"]
        assert by_code["APPLE"]["dimension_code"] == "D1"
        assert by_code["APPLE"]["dimension_table"] == "demo.dimensions"
        assert "banana" in by_code["BANANA"]["names"]
        assert by_code["BANANA"]["aliases"] == []
        assert by_code["APPLE"]["aliases"] == ["橙子"]
        assert compiled["coverage"]["alias_links"] == 1
        bundle, decision = _bundle("demo.members", "苹果", "苹果是水果类别的一个成员。")
        with pytest.raises(ValueError, match="Member or field record"):
            compile_concept(data, PROFILE, bundle, decision, {})
        alias_bundle, alias_decision = _bundle("demo.aliases", "banana", "banana 是苹果的英文叫法。")
        alias_bundle["records"][0]["fields"]["name"] = [{"column": "word", "value": "banana"}]
        with pytest.raises(ValueError, match="Member or field record"):
            compile_concept(data, PROFILE, alias_bundle, alias_decision, {})
    finally:
        data.close()


def test_member_namespace_is_a_structural_points_to_relation():
    from ontology_r2.models import BuildPlan, DerivedType, TablePlan
    from ontology_r2.structural_relations import attach_structural_relations
    data = Tables([
        ("demo.dimensions", ["dim_code", "dim_cn_name"], [("D1", "水果类别")]),
        ("demo.members", ["dim_code", "member_code", "member_cn_name"], [
            ("D1", "APPLE", "苹果"), ("D1", "BANANA", "香蕉"),
        ]),
    ])
    try:
        annotate_dimension_members(data)
        object_types = []
        tables = []
        for name in data.tables:
            evidence_id = "schema:" + name
            data.evidence[evidence_id] = {"id": evidence_id, "origin": "declared_metadata",
                                          "raw_fragment": name, "source_ref": {"table": name}}
            for column in data.tables[name]["column_names"]:
                column_id = f"schema:{name}:{column}"
                data.evidence[column_id] = {"id": column_id, "origin": "declared_metadata",
                                            "raw_fragment": column, "source_ref": {"table": name, "column": column}}
            type_id = "source_record_type:" + name
            object_types.append(DerivedType(
                id=type_id, parent="GeneralObject", label=name, definition=name,
                evidence_ids=[evidence_id], category="source_record_type"))
            tables.append(TablePlan(table=name, object_type=type_id, evidence_ids=[evidence_id]))
        updated, report = attach_structural_relations(
            data, BuildPlan(object_types=object_types, tables=tables), PROFILE)
        assert report["errors"] == []
        assert report["attached"] == 1
        dimension = next(item for item in updated.object_types if item.parent == "Dimension")
        assert dimension.id == data._registry_row_types["demo.dimensions:row:r1"]
        assert data._dimension_row_types[("demo.dimensions", (), "D1")] == dimension.id
        assert dimension.label == "水果类别"
        assert not any(item.id in ("type:dimension:demo.dimensions", "type:member:demo.members")
                       for item in updated.object_types)
        assert [item["ontology_type_id"] for item in report["concepts"]] == [dimension.id]
        relation = updated.relations[0]
        assert relation.predicate == "points_to"
        assert relation.source_table == "demo.members"
        assert relation.source_column == "dim_code"
        assert relation.target_column == "dim_code"
        assert any(data.evidence[item]["origin"] == "structural_key_reference"
                   for item in relation.evidence_ids)
        pairs = {(item["table"], item["code_column"], item["other_column"], item["kind"])
                 for item in report["column_links"]}
        assert ("demo.members", "member_code", "member_cn_name", "code_name") in pairs
        assert ("demo.dimensions", "dim_code", "dim_cn_name", "code_name") in pairs
        from ontology_r2.recall_lexicon import build_recall_artifacts
        slots = build_recall_artifacts(data, {"members": []}, report)["slots"]
        assert any(item["name"] == "code_name" and item["source_column"] == "member_code"
                   and item["target_column"] == "member_cn_name" for item in slots)
        assert any(item["name"] == "member_namespace" for item in slots)
    finally:
        data.close()


def test_member_parent_link_stays_inside_its_own_table():
    from types import SimpleNamespace
    from ontology_r2.dimension_members import materialize_structural_members
    from ontology_r2.models import BuildPlan, DerivedType
    def member(table, tenant, code, parent):
        return {"id": f"member:{table}:{tenant}:D1:{code}", "table": table, "label": code,
                "scope": {"tenant_id": tenant}, "dimension_table": "demo.dimensions",
                "dimension_code": "D1", "member_code": code, "parent_code": parent, "row_number": 1}

    members = {"members": [member("a.members", "T1", "R", None), member("a.members", "T1", "C", "R"),
                           member("a.members", "T2", "R", None), member("a.members", "T2", "C", "R"),
                           member("b.members", "T1", "R", None), member("b.members", "T1", "C", "R")]}
    shared_type = "type:dimension:shared:region"
    plan = BuildPlan(object_types=[DerivedType(id=shared_type, parent="Dimension", definition="地区",
        category="business_type", evidence_ids=["record:d1", "record:d2"])])
    data = SimpleNamespace(
        evidence={"record:d1": {"id": "record:d1"}, "record:d2": {"id": "record:d2"}},
        _dimension_row_types={
            ("demo.dimensions", (("tenant_id", "T1"),), "D1"): shared_type,
            ("demo.dimensions", (("tenant_id", "T2"),), "D1"): shared_type})
    result = materialize_structural_members(data, plan, members)
    assert {item["id"]: item["type"] for item in result["objects"]}["member:a.members:T2:D1:C"] == \
        shared_type
    edges = {(item["subject"], item["object"]) for item in result["assertions"]}
    assert edges == {("member:a.members:T1:D1:R", "member:a.members:T1:D1:C"),
                     ("member:a.members:T2:D1:R", "member:a.members:T2:D1:C"),
                     ("member:b.members:T1:D1:R", "member:b.members:T1:D1:C")}
    assert all(item["predicate"] in {relation.id for relation in plan.relation_types}
               for item in result["assertions"])
    blocked = materialize_structural_members(
        data, plan, members, max_records=0, max_assertions=0)
    assert blocked["objects"] == [] and blocked["assertions"] == []


def test_code_collision_does_not_attach_members_to_an_unrelated_table():
    data = Tables([
        ("a.dimensions", ["dim_code", "dim_cn_name"], [("1", "地区"), ("2", "产品")]),
        ("z.products", ["product_id", "product_name"], [("1", "苹果"), ("2", "香蕉")]),
        ("demo.members", ["dim_code", "member_code", "member_cn_name"], [
            ("1", "EAST", "华东"), ("1", "WEST", "华西"), ("2", "APPLE", "苹果"),
        ]),
    ])
    try:
        annotate_dimension_members(data)
        member = data.tables["demo.members"]["member_table"]
        assert member["dimension_table"] == "a.dimensions"
        assert "z.products" in member["dimension_candidates"]
        assert "dimension_registry" not in data.tables["z.products"]
    finally:
        data.close()


def test_structural_skip_is_the_column_pair_not_the_whole_table():
    from ontology_r2.structural_relations import structural_pair
    data = Tables([
        ("demo.dimensions", ["dim_code", "dim_cn_name"], [("D1", "水果类别")]),
        ("demo.members", ["dim_code", "member_code", "member_cn_name"], [
            ("D1", "APPLE", "苹果"), ("D1", "BANANA", "香蕉"),
        ]),
    ])
    try:
        annotate_dimension_members(data)
        names = {"rule": {"source": {"table": "demo.members", "field": "member_cn_name"},
                          "target": {"table": "demo.dimensions", "field": "dim_cn_name"}}}
        codes = {"rule": {"source": {"table": "demo.members", "field": "dim_code"},
                          "target": {"table": "demo.dimensions", "field": "dim_code"}}}
        assert structural_pair(data, names) is False
        assert structural_pair(data, codes) is True
    finally:
        data.close()
