"""Preserve joint reference scopes through the model, export and viewer."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from ontology_r2.incremental import ontology_from_plan
from ontology_r2.models import BuildPlan, DerivedType
from ontology_r2.storage import write_yaml
from ontology_r2.visualization import render_viewer


SCOPES = [
    {"source_table": "demo.metrics", "tenant_id": "A", "business_id": "x", "domain_code": "d1"},
    {"source_table": "demo.metrics", "tenant_id": "B", "business_id": "y", "domain_code": "d2"},
]


def metric(type_id, **kwargs):
    return DerivedType(id=type_id, parent="Metric", label="收入", definition="销售收入",
                       category="business_type", evidence_ids=[], **kwargs)


def test_joint_scopes_survive_plan_export_and_viewer_without_name_merging(tmp_path):
    legacy = metric("legacy")
    assert legacy.reference_scopes == []
    scoped = metric("shared-revenue", reference_scopes=SCOPES)
    other = metric("another-revenue")
    plan = BuildPlan.model_validate_json(BuildPlan(object_types=[scoped, other]).model_dump_json())
    ontology = ontology_from_plan(plan, {"object_roots": [{"id": "Metric"}]},
                                  None, {"tables": []}, [])
    write_yaml(tmp_path / "ontology.yaml", ontology)
    page = render_viewer(tmp_path, 10).read_text()
    payload = json.loads(page.split('<script id="result-data" type="application/json">', 1)[1]
                        .split('</script>', 1)[0])
    nodes = {node["id"]: node for node in payload["ontology_overview"]["nodes"]}
    assert set(nodes) == {"Metric", "shared-revenue", "another-revenue"}
    assert nodes["shared-revenue"]["reference_scopes"] == SCOPES
    assert nodes["another-revenue"]["reference_scopes"] == []
    assert "section(p,'引用范围',x.reference_scopes,referenceScopeCard)" in page


def test_scope_cards_keep_each_observed_combination_together():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute viewer helpers")
    page = (Path(__file__).parents[1] / "code/ontology_r2/viewer.html").read_text()
    start = page.index("function referenceScopeCard(")
    end = page.index("\n", start)
    script = """
const assert=require('node:assert/strict');
const el=(tag,content,cls)=>({tag,content,cls,fields:[]});
const field=(card,label,value)=>card.fields.push({label,value});
""" + page[start:end] + "\nconst scopes=" + json.dumps(SCOPES) + ";\n" + """
const cards=scopes.map(referenceScopeCard);
assert.equal(cards.length,2);
assert.deepEqual(cards.map(card=>card.fields.map(item=>item.value)),[
  ['demo.metrics','A','x','d1'],['demo.metrics','B','y','d2']]);
assert(cards.every(card=>card.fields[0].label==='来源表'));
"""
    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)
