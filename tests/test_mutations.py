from pathlib import Path

from factgraph.cli import load
from factgraph.mutations import (
    default_benchmark_mutations,
    evaluate_live_mutation,
    write_mutation_catalog,
)

ROOT = Path(__file__).resolve().parents[1]


def _models():
    return {p.stem: load(p) for p in (ROOT / "examples" / "portability").glob("*.fg")}


def test_mutation_catalog_removes_real_target_mechanisms_and_names_live_oracles(tmp_path):
    mutations = default_benchmark_mutations(_models())
    assert len(mutations) == 6
    assert all(m.affected_case_ids for m in mutations)

    by_id = {m.id: m for m in mutations}
    pg_fact = next(m for m in mutations if m.id.startswith("postgres-drop-fact-set-key:"))
    assert "PRIMARY KEY (a_id, b_id, c_id)" not in pg_fact.artifact

    mongo_fact = next(m for m in mutations if m.id.startswith("mongo-drop-fact-set-index:"))
    ternary = next(c for c in mongo_fact.artifact["collections"] if c["name"] == "ternary")
    assert not any(i.get("unique") and set(i["keys"]) == {"a_id", "b_id", "c_id"} for i in ternary["indexes"])

    pg_value = next(m for m in mutations if m.id.startswith("postgres-drop-value-check:"))
    assert "CHECK (age >= 0 AND age <= 130)" not in pg_value.artifact

    mongo_value = next(m for m in mutations if m.id.startswith("mongo-drop-value-validator:"))
    person = next(c for c in mongo_value.artifact["collections"] if c["name"] == "person")
    age = person["validator"]["$jsonSchema"]["properties"]["age"]
    assert "minimum" not in age and "maximum" not in age

    typedb_mandatory = next(m for m in mutations if m.id.startswith("typedb-drop-total-participation-card:"))
    assert "plays fg-r-employment:employee @card(1..)" not in typedb_mandatory.artifact
    assert "plays fg-r-employment:employee" in typedb_mandatory.artifact

    typedb_key = next(m for m in mutations if m.id.startswith("typedb-drop-key:"))
    assert "@key" not in typedb_key.artifact

    catalog = write_mutation_catalog(_models(), tmp_path)
    assert catalog["mutation_count"] == 6
    assert (tmp_path / "catalog.json").is_file()
    assert all((tmp_path / e["directory"] / e["artifact_file"]).is_file() for e in catalog["mutations"])


def test_live_mutation_evaluator_requires_valid_baseline_and_a_flipped_named_case():
    mutation = default_benchmark_mutations(_models())[0]
    case_id = mutation.affected_case_ids[0]
    baseline = {"status": "completed", "passed": True, "results": [{"case_id": case_id, "passed": True}]}
    mutated = {"status": "completed", "passed": False, "results": [{"case_id": case_id, "passed": False}]}
    result = evaluate_live_mutation(mutation, baseline, mutated)
    assert result["passed"] is True
    assert result["status"] == "mutation_detected"

    survivor = {"status": "completed", "passed": True, "results": [{"case_id": case_id, "passed": True}]}
    result = evaluate_live_mutation(mutation, baseline, survivor)
    assert result["passed"] is False
    assert result["status"] == "survived_mutation"

    unavailable = {"status": "not_run", "results": []}
    result = evaluate_live_mutation(mutation, unavailable, unavailable)
    assert result["passed"] is False
    assert result["status"] == "not_observed"
