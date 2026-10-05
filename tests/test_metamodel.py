from __future__ import annotations

import json
from pathlib import Path

from factgraph.cli import main
from factgraph.metamodel import (
    build_metamodel,
    decode_model,
    encode_model,
    population_dict,
    reification_report,
    self_host_report,
    metamodel_source,
)
from factgraph.normalize import normalize_model
from factgraph.parser import parse_model
from factgraph.printer import print_model
from factgraph.validate import validate_model

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.fg"))


def load(path: Path):
    return normalize_model(parse_model(path.read_text(encoding="utf-8")))


def test_metamodel_is_an_ordinary_factgraph_model_and_print_parse_stable():
    mm = build_metamodel()
    assert not [d for d in validate_model(mm) if d.severity.value == "error"]
    canonical = print_model(mm)
    assert canonical == metamodel_source()
    reparsed = normalize_model(parse_model(canonical))
    assert mm.semantically_equal(reparsed)


def test_metamodel_describes_itself_and_reencoding_is_fixed_point():
    report = self_host_report()
    assert report["population_validation_errors"] == []
    assert report["semantic_roundtrip_equal"] is True
    assert report["manifest_roundtrip_equal"] is True
    assert report["second_encoding_identical"] is True
    assert report["self_population_row_count"] > 500


def test_every_example_roundtrips_through_core_and_envelope_metamodel_populations():
    for path in EXAMPLES:
        model = load(path)
        core = encode_model(model, include_envelope=False)
        envelope = encode_model(model, include_envelope=True)
        assert not [d for d in validate_model(core) if d.severity.value == "error"], path.name
        assert not [d for d in validate_model(envelope) if d.severity.value == "error"], path.name
        core_decoded = decode_model(core, include_envelope=False)
        full_decoded = decode_model(envelope, include_envelope=True)
        assert model.semantically_equal(core_decoded), path.name
        assert model.manifest_dict() == full_decoded.manifest_dict(), path.name


def test_population_encoding_is_deterministic():
    model = load(ROOT / "examples" / "richer_constraints.fg")
    a = population_dict(encode_model(model, include_envelope=True))
    b = population_dict(encode_model(model, include_envelope=True))
    assert a == b


def test_reification_report_separates_semantic_core_from_compiler_envelope():
    model = load(ROOT / "examples" / "employment.fg")
    report = reification_report(model)
    assert report["core_population"]["semantic_roundtrip_equal"] is True
    assert report["envelope_population"]["semantic_roundtrip_equal"] is True
    assert report["envelope_population"]["manifest_roundtrip_equal"] is True
    assert report["envelope_population"]["row_count"] >= report["core_population"]["row_count"]


def test_cli_metamodel_and_reify_write_file_handoffs(tmp_path: Path):
    meta_dir = tmp_path / "metamodel"
    assert main(["metamodel", "--out-dir", str(meta_dir)]) == 0
    expected_meta = {
        "metamodel.fg",
        "semantic.json",
        "manifest.json",
        "validation.json",
        "incidence.json",
        "self.population.json",
        "self.populated.metamodel.manifest.json",
        "self_host.json",
    }
    assert expected_meta.issubset({p.name for p in meta_dir.iterdir()})
    self_report = json.loads((meta_dir / "self_host.json").read_text())
    assert self_report["second_encoding_identical"] is True

    reify_dir = tmp_path / "reify"
    assert main(["reify", str(ROOT / "examples" / "warehouse.fg"), "--out-dir", str(reify_dir)]) == 0
    expected_reify = {
        "core.population.json",
        "envelope.population.json",
        "populated.metamodel.manifest.json",
        "recovered.semantic.json",
        "recovered.manifest.json",
        "recovered.normalized.fg",
        "roundtrip.json",
    }
    assert expected_reify.issubset({p.name for p in reify_dir.iterdir()})
    report = json.loads((reify_dir / "roundtrip.json").read_text())
    assert report["core_population"]["semantic_roundtrip_equal"] is True
    assert report["envelope_population"]["manifest_roundtrip_equal"] is True
