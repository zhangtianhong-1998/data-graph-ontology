"""Local formula codes bind through their source registry scope after sharing."""
from contextlib import contextmanager

import pytest

from ontology_r2.calculation_contracts import calculation_relation_errors, enrich_calculation_contracts
from ontology_r2.models import DerivedType
from test_registry_rows import _compile, _data, _row


@contextmanager
def case(measures=None, metrics=None):
    data = _data(
        ("alpha.measures", "Measure", measures or [
            _row("M1", tenant="T1", business="MEASURE", domain="DEFINITIONS"),
            _row("M9", tenant="T2", business="MEASURE", domain="DEFINITIONS")]),
        ("alpha.metrics", "Metric", metrics or [
            _row("K1", tenant="T1", formula="M1 * 2", business="METRIC", domain="REPORTING"),
            _row("K2", tenant="T2", formula="M9 * 2", business="METRIC", domain="REPORTING")]))
    try:
        plan, group = _compile(data)
        yield data, plan, group
    finally:
        data.close()


def metric_calculations(plan, result):
    metric_ids = {item.id for item in plan.object_types if item.parent == "Metric"}
    return [item for item in result["calculations"] if item["source_type_id"] in metric_ids]


def relation_for(dependency):
    return DerivedType(id="test:dependency", parent="depends_on", definition="source formula",
                       category="business_relation_type", predicate_name="calculation_dependency",
                       domain=[dependency["source_type_id"]], range=[dependency["target_type_id"]],
                       evidence_scope="complete_calculation_definition",
                       semantic_parameters={"operand_role": dependency["operand_role"]},
                       evidence_ids=dependency["evidence_ids"])


def test_different_local_codes_share_formula_contract_without_cross_scope_evidence():
    with case() as (data, plan, group):
        result = enrich_calculation_contracts(data, plan, group)
        calculations = metric_calculations(plan, result)
        assert len(calculations) == 2
        assert {item["source_type_id"] for item in calculations} == {
            data._registry_row_types["alpha.metrics:row:r1"]}
        assert all(item["status"] == "accepted" for item in calculations)
        assert {item["raw_formula"] for item in calculations} == {"M1 * 2", "M9 * 2"}
        assert len(result["dependencies"]) == 2
        for calculation in calculations:
            binding = calculation["bindings"][0]
            source_scope = calculation["reference_scopes"][0]
            assert source_scope["business_id"] == "METRIC"
            for proof in binding["binding_evidence"]:
                target_scope = data._registry_reference_scopes[proof["source_ref"]["record_id"]]
                assert source_scope["tenant_id"] == target_scope["tenant_id"]
                assert target_scope["business_id"] == "MEASURE"
                assert target_scope["domain_code"] != source_scope["domain_code"]
        for dependency in result["dependencies"]:
            assert calculation_relation_errors(data, relation_for(dependency), plan.object_types) == []
        # A combined relation may cite both local formulas when their bound trees agree.
        combined = relation_for(result["dependencies"][0])
        combined.evidence_ids = sorted({eid for dep in result["dependencies"] for eid in dep["evidence_ids"]})
        assert calculation_relation_errors(data, combined, plan.object_types) == []


def test_same_local_code_in_different_tenants_binds_each_original_definition():
    with case(measures=[_row("M1", tenant="T1", formula="gross_sales"),
                        _row("M1", tenant="T2", formula="net_sales")],
              metrics=[_row("K1", tenant="T1", formula="M1"),
                       _row("K2", tenant="T2", formula="M1")]) as (data, plan, group):
        result = enrich_calculation_contracts(data, plan, group)
        calculations = metric_calculations(plan, result)
        assert len(calculations) == 2 and all(item["status"] == "accepted" for item in calculations)
        for row in (1, 2):
            metric_id = data._registry_row_types[f"alpha.metrics:row:r{row}"]
            measure_id = data._registry_row_types[f"alpha.measures:row:r{row}"]
            dependency = next(item for item in result["dependencies"] if item["source_type_id"] == metric_id)
            assert dependency["target_type_id"] == measure_id
            assert calculation_relation_errors(data, relation_for(dependency), plan.object_types) == []


@pytest.mark.parametrize("measures", [
    [_row("M9", tenant="T2")],
    [_row("M9", tenant="T1", formula="gross_sales"), _row("M9", tenant="T1", formula="net_sales")],
])
def test_missing_or_ambiguous_local_code_does_not_fall_back_to_global_names(measures):
    with case(measures=measures, metrics=[_row("K1", tenant="T1", formula="M9")]) as (data, plan, group):
        result = enrich_calculation_contracts(data, plan, group)
        assert metric_calculations(plan, result)[0]["status"] == "unresolved"
        assert result["dependencies"] == []


@pytest.mark.parametrize("damage", ["formula", "target_code", "target_snapshot", "bindings"])
def test_binding_cache_cannot_replace_current_source_evidence(damage):
    with case() as (data, plan, group):
        original = enrich_calculation_contracts(data, plan, group)
        dependency = next(item for item in original["dependencies"] if item["symbol"] == "M1")
        formula_id = next(eid for eid in dependency["evidence_ids"]
                          if data.evidence[eid]["source_ref"].get("record_id") == "alpha.metrics:row:r1"
                          and data.evidence[eid]["source_ref"]["column"] == "formula")
        code_id = dependency["binding_evidence"][0]["evidence_id"]
        if damage == "formula":
            data.evidence[formula_id]["raw_fragment"] = "M1 + 2"
        elif damage == "target_code":
            data.evidence[code_id]["raw_fragment"] = "UNRELATED"
        elif damage == "target_snapshot":
            data.evidence[code_id]["source_ref"]["snapshot_id"] = "stale"
        else:
            data._registry_formula_bindings.pop("alpha.metrics:row:r1")
        result = enrich_calculation_contracts(data, plan, group)
        calculation = next(item for item in metric_calculations(plan, result)
                           if item["source_refs"][0]["record_id"] == "alpha.metrics:row:r1")
        assert calculation["status"] == "unresolved"
        assert calculation_relation_errors(data, relation_for(dependency), plan.object_types)


def test_relation_replay_rejects_foreign_scope_code_evidence_and_wrong_operand_role():
    with case() as (data, plan, group):
        result = enrich_calculation_contracts(data, plan, group)
        dependency = next(item for item in result["dependencies"] if item["symbol"] == "M1")
        foreign = next(item for item in result["dependencies"] if item["symbol"] == "M9")
        relation = relation_for(dependency)
        own_code = dependency["binding_evidence"][0]["evidence_id"]
        relation.evidence_ids.remove(own_code)
        relation.evidence_ids.append(foreign["binding_evidence"][0]["evidence_id"])
        assert calculation_relation_errors(data, relation, plan.object_types)
        relation = relation_for(dependency)
        relation.semantic_parameters["operand_role"] = "subtrahend"
        assert calculation_relation_errors(data, relation, plan.object_types)


def test_unresolved_operand_tasks_keep_their_original_reference_scopes():
    with case(metrics=[_row("K1", tenant="T1", formula="UNKNOWN * 2"),
                       _row("K2", tenant="T2", formula="UNKNOWN * 2")]) as (data, plan, group):
        result = enrich_calculation_contracts(data, plan, group)
        tasks = [task for task in result["binding_tasks"] if task["symbol"] == "UNKNOWN"]
        assert len(tasks) == 2
        assert {task["reference_scopes"][0]["tenant_id"] for task in tasks} == {"T1", "T2"}
        assert all(not task["automatic_relation_created"] for task in tasks)
