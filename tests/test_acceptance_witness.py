from pathlib import Path

from factgraph.acceptance_witness import synthesize_acceptance_probes
from factgraph.audit import obligations
from factgraph.cli import load
from factgraph.population import validate_population

ROOT = Path(__file__).resolve().parents[1]


def test_value_range_acceptance_probes_cover_both_declared_boundaries_and_are_source_valid():
    model = load(ROOT / "examples" / "portability" / "value_range.fg")
    ob = next(o for o in obligations(model) if o.kind == "value")
    probes = synthesize_acceptance_probes(model, ob.id, ob.kind)
    assert [p.label for p in probes] == ["source lower boundary 0", "source upper boundary 130"]
    literals = []
    for probe in probes:
        assert validate_population(model, probe.population) == []
        literals.extend(probe.population.values.values())
    assert 0 in literals
    assert 130 in literals


def test_total_participation_acceptance_probe_contains_actual_participation_and_is_source_valid():
    model = load(ROOT / "examples" / "portability" / "total_participation.fg")
    ob = next(o for o in obligations(model) if o.kind == "mandatory" and "Employment:employee" in o.id)
    probes = synthesize_acceptance_probes(model, ob.id, ob.kind)
    assert len(probes) == 1
    probe = probes[0]
    assert probe.label == "source-valid participation in Employment.employee"
    assert validate_population(model, probe.population) == []
    assert len(probe.population.memberships["entity:Person"]) == 1
    assert len(probe.population.facts["fact:Employment"]) == 1


def test_relationship_acceptance_probe_families_are_source_valid():
    cases = [
        ("binary_role_uniqueness.fg", "uniqueness"),
        ("frequency_min_one_max_two.fg", "frequency"),
        ("subtype.fg", "subtype"),
        ("symmetry.fg", "ring"),
        ("unordered_roles.fg", "unordered_role_group"),
    ]
    for filename, kind in cases:
        model = load(ROOT / "examples" / "portability" / filename)
        ob = next(o for o in obligations(model) if o.kind == kind)
        probes = synthesize_acceptance_probes(model, ob.id, ob.kind)
        assert probes, (filename, kind)
        for probe in probes:
            assert validate_population(model, probe.population) == [], (filename, probe.label)


def test_set_relation_acceptance_probes_cover_subset_equality_and_exclusion():
    model = load(ROOT / "examples" / "portability" / "set_constraints.fg")
    for kind in ("subset", "equality", "exclusion"):
        ob = next(o for o in obligations(model) if o.kind == kind)
        probes = synthesize_acceptance_probes(model, ob.id, ob.kind)
        assert len(probes) == 1
        assert validate_population(model, probes[0].population) == []
