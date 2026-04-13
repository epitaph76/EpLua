import json
from pathlib import Path

from packages.retrieval.selector import select_retrieval_pack


def test_select_retrieval_pack_returns_bounded_guidance_for_simple_extraction() -> None:
    pack = select_retrieval_pack(
        archetype="simple_extraction",
        output_mode="raw_lua",
        risk_tags=["array_indexing", "empty_array", "json_wrapper"],
    )

    assert len(pack.examples) <= 2
    example_ids = {example["id"] for example in pack.examples}
    assert "case-01-last-array-item" in example_ids
    assert pack.archetype_template["archetype"] == "simple_extraction"
    assert pack.format_rules["output_mode"] == "raw_lua"
    assert "Return only Lua code." in pack.format_rules["rules"]


def test_select_retrieval_pack_reads_examples_from_examples_directory() -> None:
    pack = select_retrieval_pack(
        archetype="transformation",
        output_mode="raw_lua",
        risk_tags=["numeric_transform", "wrong_root"],
    )

    assert len(pack.examples) <= 2
    example_ids = {example["id"] for example in pack.examples}
    assert "case-02-try-counter" in example_ids
    assert any(example_id.startswith("synthetic-") for example_id in example_ids)


def test_every_registered_archetype_has_a_retrieval_template() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    registry_path = repo_root / "packages" / "task-archetypes" / "registry.json"

    with registry_path.open("r", encoding="utf-8") as handle:
        registry = json.load(handle)

    for archetype in registry:
        pack = select_retrieval_pack(
            archetype=archetype,
            output_mode=registry[archetype]["allowed_output_modes"][0],
            risk_tags=[],
        )
        assert pack.archetype_template is not None
        assert pack.archetype_template["archetype"] == archetype


def test_synthetic_dataset_contains_at_least_twenty_examples() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    synthetic_path = repo_root / "knowledge" / "examples" / "synthetic_cases.json"

    with synthetic_path.open("r", encoding="utf-8") as handle:
        synthetic_dataset = json.load(handle)

    assert len(synthetic_dataset["examples"]) >= 20
