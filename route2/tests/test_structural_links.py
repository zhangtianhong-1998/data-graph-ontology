"""Structural links between typed rows, without a model and without duplicate nodes."""
from ontology_r2.dimension_members import annotate_dimension_members, compile_dimension_members
from ontology_r2.models import BuildPlan, DerivedType, TablePlan
from ontology_r2.quantity_registry import annotate_quantity_registries
from ontology_r2.structural_relations import attach_structural_relations, materialize_class_rows
from ontology_r2.table_keys import annotate_table_keys
from test_group_incremental import PROFILE
from test_table_keys import Tables


def catalog():
    tenants = ("T1", "T2")
    rows = lambda make: [row for tenant in tenants for row in make(tenant)]
    return Tables([
        ("demo.market_api", ["api_id", "api_code", "api_name", "tenant_id"],
         rows(lambda t: [("1", "SVC1", "苹果查询", t), ("2", "SVC2", "香蕉查询", t)])),
        ("demo.dashboard_card", ["card_id", "card_code", "card_name", "metric_code", "dim_code", "tenant_id"],
         rows(lambda t: [("1", "VIEW1", "华东概览", "MET1", "D1", t), ("2", "VIEW2", "华南概览", "MET2", "D1", t),
                         ("3", "VIEW3", "西区概览", "MET1", "D1", t)])),
        ("demo.metric_detail", ["metric_id", "code", "name", "calculation_formula", "unit", "tenant_id"],
         rows(lambda t: [("1", "MET1", "销售收入", "a + b", "元", t), ("2", "MET2", "销售成本", "c - d", "元", t)])),
        ("demo.measure_def", ["measure_id", "measure_code", "measure_name", "inference_type", "tenant_id"],
         rows(lambda t: [("1", "MES1", "金额", "SUM", t), ("2", "MES2", "数量", "SUM", t)])),
        ("demo.metric_attr", ["attr_id", "metric_code", "measure_code", "tenant_id"],
         rows(lambda t: [("1", "MET1", "MES1", t), ("2", "MET2", "MES1", t)])),
        ("demo.dim_definition", ["dim_def_id", "dim_code", "dim_cn_name", "tenant_id"],
         rows(lambda t: [("1", "D1", "地区", t), ("2", "D2", "品类", t)])),
        ("demo.dim_member", ["member_id", "dim_code", "member_code", "member_cn_name", "parent_member_code", "tenant_id"],
         rows(lambda t: [("1", "D1", "ROOT", "全部", None, t), ("2", "D1", "EAST", "华东", "ROOT", t),
                         ("3", "D2", "ROOT", "全部", None, t), ("4", "D2", "APPLE", "苹果", "ROOT", t)])),
        ("demo.input_param", ["input_param_id", "business_id", "business_type", "param_name", "tenant_id"],
         rows(lambda t: [("1", "1", "API", "region", t), ("2", "2", "接口", "fruit", t),
                         ("3", "1", "CARD", "metric", t), ("4", "2", "卡片", "period", t)])),
        ("demo.output_param", ["output_param_id", "business_id", "business_type", "param_name", "tenant_id"],
         rows(lambda t: [("1", "1", "API", "amount", t), ("2", "2", "api", "volume", t),
                         ("3", "1", "card", "ratio", t), ("4", "2", "CARD", "trend", t)])),
        ("demo.param_rule", ["rule_id", "business_id", "business_type", "param_id", "param_direction",
                             "source_type", "source_field", "field_rule", "tenant_id"],
         rows(lambda t: [("1", "1", "API", "1", "IN", "D1", "member_code", '{"allowed":["EAST"]}', t),
                         ("2", "2", "接口", "2", "IN", "指标", "MET2", '["MET1","MET2"]', t),
                         ("3", "1", "CARD", "3", "OUT", "Metric", "MET1", '["MET1"]', t),
                         ("4", "2", "卡片", "4", "OUT", "D2", "member_code", '["APPLE"]', t),
                         ("5", "1", "api", "1", "IN", "D1", "member_code", '["ROOT"]', t),
                         ("6", "1", "card", "3", "OUT", "Metric", "MET2", '["MET2"]', t)])),
    ])


def attached(data):
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
        object_types.append(DerivedType(id="source_record_type:" + name, parent="GeneralObject", label=name,
                                        definition=name, category="source_record_type",
                                        evidence_ids=["schema:" + name]))
        tables.append(TablePlan(table=name, object_type="source_record_type:" + name,
                                evidence_ids=["schema:" + name]))
    return attach_structural_relations(data, BuildPlan(object_types=object_types, tables=tables), PROFILE)


def test_type_values_resolve_resources_registries_and_member_lists():
    data = catalog()
    try:
        plan, report = attached(data)
        assert report["errors"] == []
        by_id = {item.id: item for item in plan.relations}
        api = by_id["plan:demo.input_param.business_id-demo.market_api.api_id[business_type=API]"]
        assert set(api.selector.values) == {"API", "接口"}
        assert api.scope_bindings == {"tenant_id": "tenant_id"}
        card = by_id["plan:demo.input_param.business_id-demo.dashboard_card.card_id[business_type=CARD]"]
        assert set(card.selector.values) == {"CARD", "卡片"}
        labels = {item.id: item.label for item in plan.object_types}
        assert labels["type:class:demo.market_api"] == "API"
        assert labels["type:class:demo.dashboard_card"] == "CARD"
        dimension = by_id["plan:demo.param_rule.source_type-demo.dim_definition.dim_code"]
        assert set(dimension.selector.values) == {"D1", "D2"}
        metric = next(item for item in plan.relations
                      if item.source_column == "source_field" and item.target_table == "demo.metric_detail")
        assert set(metric.selector.values) == {"Metric", "指标"}
        members = [item for item in plan.relations
                   if item.source_column == "field_rule" and item.target_table == "demo.dim_member"]
        assert members and all(item.scope_bindings == {"tenant_id": "tenant_id", "source_type": "dim_code"}
                               for item in members)
        assert {tuple(item.source_path) for item in members} == {("[]",), ("allowed", "[]")}
        rules = {item.target_table: item for item in plan.relations
                 if item.source_column == "param_id" and item.source_table == "demo.param_rule"}
        assert rules["demo.input_param"].selector.values == ["IN"]
        assert rules["demo.output_param"].selector.values == ["OUT"]
        assert rules["demo.input_param"].scope_bindings == {"tenant_id": "tenant_id", "business_id": "business_id"}
    finally:
        data.close()


def test_typed_rows_are_connected_by_type_level_relations():
    data = catalog()
    try:
        plan, report = attached(data)
        relations = [item for item in plan.relation_types if item.category == "business_relation_type"]
        pairs = {(item.domain[0], item.range[0], item.predicate_name) for item in relations}
        assert (data._registry_row_types["demo.metric_detail:row:r2"],
                data._registry_row_types["demo.measure_def:row:r1"],
                "measure_binding") in pairs
        assert ("type:class:demo.dashboard_card", data._registry_row_types["demo.dim_definition:row:r3"],
                "resource_scope_binding") in pairs
        assert any(domain == "type:class:demo.dashboard_card" and range_.startswith("type:metric:")
                   for domain, range_, _ in pairs)
        edges = {(item["subject"], item["object"]) for item in report["assertions"]}
        assert (data._registry_record_to_object["demo.metric_detail:row:r2"],
                data._registry_record_to_object["demo.measure_def:row:r1"]) in edges
        for edge in report["assertions"]:
            for witness in edge["reference_scopes"]:
                assert witness["source_reference_scope"]["tenant_id"] == witness["target_reference_scope"]["tenant_id"]
    finally:
        data.close()


def test_each_source_row_is_one_node():
    data = catalog()
    try:
        plan, report = attached(data)
        members = compile_dimension_members(data)["members"]
        assert all(item["record_id"] == f"{item['table']}:row:r{item['row_number']}" for item in members)
        classes = materialize_class_rows(data, plan)["objects"]
        ids = [item["id"] for item in classes]
        assert len(ids) == len(set(ids)) == 10
        assert {item["type"] for item in classes} == {"type:class:demo.market_api", "type:class:demo.dashboard_card"}
        skipped = materialize_class_rows(data, plan, {"demo.market_api:row:r1"})["objects"]
        assert "demo.market_api:row:r1" not in {item["id"] for item in skipped}
    finally:
        data.close()


def test_restored_plan_rebuilds_registry_maps_and_scoped_links_without_duplicates():
    data = catalog()
    try:
        plan, report = attached(data)
        expected_objects = dict(data._registry_record_to_object)
        data._registry_record_to_object, data._registry_row_types = {}, {}
        data._class_tables, data._dimension_row_types = {}, {}
        restored, replay = attach_structural_relations(data, plan, PROFILE)
        assert replay["errors"] == []
        assert data._registry_record_to_object == expected_objects
        assert len(restored.object_types) == len(plan.object_types)
        assert len(restored.relations) == len(plan.relations)
        assert len(replay["assertions"]) == len(report["assertions"])
        assert replay["class_types"] == report["class_types"]
        assert len(replay["record_alignments"]) == len(expected_objects)
    finally:
        data.close()
