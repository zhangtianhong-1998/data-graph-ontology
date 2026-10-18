"""Shared definitions retain local references without relaxing lookup scopes."""
import asyncio
import json

from ontology_r2.models import BuildPlan, DerivedType, RelationPlan, SourceProperty, TablePlan
from ontology_r2.ontology_instances import materialize_ontology_instances
from ontology_r2.relations import Extractor
from ontology_r2.relation_contract import canonical_relation_id
from ontology_r2.storage import Dataset, Sink, record_evidence_id
from ontology_r2.structural_relations import _lookup_key, _row_index
from test_semantic_cards import _table


def shared_case(tmp_path):
    root = tmp_path / "input"
    _table(root, "measure_definition", {"id": "ID", "code": "编码", "name": "名称", "tenant": "租户"}, [
        {"id": "1", "code": "M1", "name": "收入", "tenant": "T1"},
        {"id": "2", "code": "M2", "name": "成本", "tenant": "T1"},
        {"id": "3", "code": "M9", "name": "收入", "tenant": "T2"},
        {"id": "4", "code": "M1", "name": "成本", "tenant": "T2"}])
    _table(root, "local_reference", {"id": "ID", "code": "引用", "tenant": "租户"}, [
        {"id": "1", "code": "M1", "tenant": "T1"},
        {"id": "2", "code": "M1", "tenant": "T2"},
        {"id": "3", "code": "M9", "tenant": "T2"},
        {"id": "4", "code": "M1", "tenant": ""}])
    work = tmp_path / "work"
    work.mkdir()
    data = Dataset(root, work)
    data._registry_row_types, data._registry_record_to_object, data._registry_reference_scopes = {}, {}, {}
    definitions, concepts, alignments = {}, {}, []
    table = "fruit.measure_definition"
    for row in data.rows(table):
        rid = data.record_id(table, row)
        type_id = "type:measure:shared:" + row["name"]
        data._registry_row_types[rid] = type_id
        data._registry_record_to_object[rid] = "registry_object:" + type_id[len("type:"):]
        data._registry_reference_scopes[rid] = {"source_table": table, "tenant": row["tenant"], "code": row["code"]}
        definition = definitions.setdefault(type_id, DerivedType(id=type_id, parent="Measure", label=row["name"],
            definition=row["name"], category="business_type", evidence_ids=[]))
        proofs = []
        for field, role in (("name", "name"), ("code", "identity")):
            eid = record_evidence_id(rid, field)
            data.evidence[eid] = {"id": eid, "origin": "observed_record", "raw_fragment": row[field],
                "source_ref": {"table": table, "record_id": rid, "row": row["__r2_row"], "column": field,
                               "snapshot_id": data.snapshot_id}}
            definition.source_properties.append(SourceProperty(role=role, source_table=table, source_column=field,
                                                               evidence_ids=[eid]))
            definition.evidence_ids.append(eid)
            proofs.append(eid)
        concepts[type_id] = {"id": "concept:" + type_id, "ontology_type_id": type_id, "ontology_level": "type"}
        alignments.append({"id": "alignment:" + rid, "source_record_id": rid,
            "concept_id": "concept:" + type_id, "mapping_kind": "exact", "evidence_ids": proofs})
    tables = [TablePlan(table=name, object_type="source_record_type:" + name,
                        label_column="code", attributes={"code": "code"}, evidence_ids=[])
              for name in data.tables]
    source_types = [DerivedType(id=item.object_type, parent="GeneralObject", label=item.table,
                               definition=item.table, category="source_record_type", evidence_ids=[])
                    for item in tables]
    plan = BuildPlan(object_types=[*source_types, *definitions.values()], tables=tables,
        relations=[RelationPlan(id="local-reference", source_table="fruit.local_reference", target_table=table,
            source_column="code", target_column="code", scope_bindings={"tenant": "tenant"},
            mode="identifier", predicate="points_to", semantics="allowed_member", evidence_ids=[])])
    return data, plan, list(concepts.values()), alignments


def test_registry_instances_merge_sources_but_keep_each_local_binding(tmp_path):
    data, plan, concepts, alignments = shared_case(tmp_path)
    try:
        result = materialize_ontology_instances(data, plan, concepts=concepts, record_alignments=alignments)
        assert len(result["objects"]) == 2
        assert len(result["source_mappings"]) == 4
        assert result["coverage"]["source_records_read"] == 4
        assert result["coverage"]["shared_registry_objects"] == 2
        assert result["coverage"]["registry_source_bindings"] == 4
        assert result["pending"] == []
        for node in result["objects"]:
            assert len(node["source_refs"]) == len(node["reference_bindings"]) == 2
            assert {scope["tenant"] for scope in node["reference_scopes"]} == {"T1", "T2"}
            assert {item["source_record_id"] for item in node["properties"]} == {
                ref["record_id"] for ref in node["source_refs"]}
            assert all(binding["properties"] and binding["evidence_ids"] for binding in node["reference_bindings"])
        limited = materialize_ontology_instances(data, plan, concepts=concepts, record_alignments=alignments,
                                                max_records=1)
        assert limited["coverage"]["source_records_read"] == 1
        assert limited["coverage"]["source_records_omitted"] == 3
    finally:
        data.close()


def test_endpoint_extraction_reuses_shared_nodes_and_resolves_codes_inside_tenant(tmp_path):
    data, plan, concepts, alignments = shared_case(tmp_path)
    output = tmp_path / "output"
    (output / "work").mkdir(parents=True)
    sink = Sink(output)
    try:
        result = materialize_ontology_instances(data, plan, concepts=concepts, record_alignments=alignments)
        for node in result["objects"]:
            sink.put("objects", node)
        extractor = Extractor(data, sink, plan, None, {})
        stats = asyncio.run(extractor.execute())
        objects = [json.loads(row[0]) for row in sink.db.execute("SELECT body FROM items WHERE kind='objects'")]
        registry = [node for node in objects if node.get("instance_kind") == "shared_registry_object"]
        assert len(registry) == 2
        assert len(objects) == 5  # Two definitions, three resolved reference rows.
        assert all(len(node["reference_bindings"]) == len(node["source_refs"]) == 2 for node in registry)
        assert not any(node["id"].startswith("fruit.measure_definition:") for node in objects)
        edges = [json.loads(row[0]) for row in sink.db.execute("SELECT body FROM items WHERE kind='assertions'")]
        references = [edge for edge in edges if edge.get("plan_id") == "local-reference"]
        targets = {edge["source_record_pair"]["source"]: edge["object"] for edge in references}
        assert targets["fruit.local_reference:1:r1"].endswith("收入")
        assert targets["fruit.local_reference:2:r2"].endswith("成本")
        assert targets["fruit.local_reference:3:r3"].endswith("收入")
        assert stats["unresolved"] == 1  # Missing scope is not a global-code lookup.
        rules = [json.loads(row[0]) for row in sink.db.execute("SELECT body FROM items WHERE kind='rules'")]
        assert len(rules) == 3
        assert all(rule["members"] == [targets[rule["subject"]]] for rule in rules)
        assert all(rule["source_member_record_ids"] for rule in rules)
    finally:
        sink.close()
        data.close()


def test_structural_lookup_rejects_missing_scope_and_duplicate_local_key(tmp_path):
    data, _, _, _ = shared_case(tmp_path)
    try:
        assert _lookup_key({"code": "M1", "tenant": ""}, "code", {"tenant": "tenant"}) is None
        table = "fruit.measure_definition"
        index = _row_index(data, table, "code", {"tenant": "tenant"}, 100)
        assert index[("M1", "T1")] != index[("M1", "T2")]
        sql_name = data.tables[table]["sql_name"]
        data.db.execute(f'INSERT INTO "{sql_name}" SELECT 5, id, code, name, tenant FROM "{sql_name}" WHERE __r2_row=1')
        assert ("M1", "T1") not in _row_index(data, table, "code", {"tenant": "tenant"}, 100)
    finally:
        data.close()


def test_relation_replay_uses_second_source_of_each_shared_node(tmp_path):
    data, plan, concepts, alignments = shared_case(tmp_path)
    try:
        table = "fruit.measure_definition"
        info = data.tables[table]
        data.db.execute(f'ALTER TABLE "{info["sql_name"]}" ADD COLUMN ref_code VARCHAR')
        data.db.execute(f'UPDATE "{info["sql_name"]}" SET ref_code=\'M1\' WHERE id=\'3\'')
        info["column_names"].append("ref_code")
        pair = {"source": "fruit.measure_definition:3:r3", "target": "fruit.measure_definition:4:r4"}
        proof = record_evidence_id(pair["source"], "ref_code")
        data.evidence[proof] = {"id": proof, "origin": "observed_record", "raw_fragment": "M1",
            "source_ref": {"record_id": pair["source"], "table": table, "row": 3,
                           "column": "ref_code", "snapshot_id": data.snapshot_id}}
        source_type, target_type = (data._registry_row_types[pair[side]] for side in ("source", "target"))
        base = DerivedType(id="source-reference", parent="points_to", label="points_to", definition="来源引用",
            domain=[source_type], range=[target_type], evidence_ids=[proof],
            evidence_scope="sample_semantic_with_full_technical_check")
        relation_id = canonical_relation_id("points_to", source_type, target_type)
        promoted = base.model_copy(update={"id": relation_id, "category": "business_relation_type"})
        execution = RelationPlan(id="witnessed-reference", source_table=table, target_table=table,
            source_column="ref_code", target_column="code", scope_bindings={"tenant": "tenant"},
            mode="identifier", predicate=base.id, evidence_ids=[proof],
            evidence_scope="sample_semantic_with_full_technical_check", witness_snapshot_id=data.snapshot_id,
            witnessed_pairs=[{"source_record_id": pair["source"], "target_record_id": pair["target"]}])
        plan.relation_types, plan.relations = [base, promoted], [execution]
        assertion = {"id": "witnessed-edge", "predicate": relation_id, "source_record_pair": pair,
            "source_relation_plan_id": execution.id, "subject_type": source_type, "object_type": target_type,
            "identity_scope": "input_snapshot", "evidence_ids": [proof], "decision": {"status": "accepted"}}
        result = materialize_ontology_instances(data, plan, concepts=concepts, record_alignments=alignments,
                                               record_relations=[assertion])
        assert len(result["objects"]) == 2 and len(result["assertions"]) == 1
        edge = result["assertions"][0]
        assert edge["subject"] == data._registry_record_to_object[pair["source"]]
        assert edge["object"] == data._registry_record_to_object[pair["target"]]
        assert edge["source_record_pair"] == pair
        forged = {**assertion, "source_record_pair": {**pair, "target": "fruit.measure_definition:1:r1"}}
        assert materialize_ontology_instances(data, plan, concepts=concepts, record_alignments=alignments,
                                              record_relations=[forged])["assertions"] == []
    finally:
        data.close()
