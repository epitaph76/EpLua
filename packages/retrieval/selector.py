from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLES_DIR = _REPO_ROOT / "knowledge" / "examples"
_TEMPLATES_PATH = _REPO_ROOT / "knowledge" / "templates" / "domain_prompt_templates.json"
_ARCHETYPE_TEMPLATE_DIR = _REPO_ROOT / "knowledge" / "archetypes"


@dataclass(frozen=True)
class RetrievalPack:
    examples: tuple[dict[str, Any], ...]
    archetype_template: dict[str, Any] | None
    format_rules: dict[str, Any] | None

    def has_guidance(self) -> bool:
        return bool(self.examples or self.archetype_template or self.format_rules)


def select_retrieval_pack(
    *,
    archetype: str,
    output_mode: str,
    risk_tags: list[str] | tuple[str, ...] | None = None,
) -> RetrievalPack:
    normalized_risk_tags = tuple(dict.fromkeys(risk_tags or ()))
    examples = _select_examples(archetype=archetype, output_mode=output_mode, risk_tags=normalized_risk_tags)

    return RetrievalPack(
        examples=examples,
        archetype_template=_load_archetype_template(archetype),
        format_rules=_load_format_rules(output_mode, normalized_risk_tags),
    )


def _select_examples(
    *,
    archetype: str,
    output_mode: str,
    risk_tags: tuple[str, ...],
) -> tuple[dict[str, Any], ...]:
    scored_examples: list[tuple[int, str, dict[str, Any]]] = []
    requested_risks = set(risk_tags)

    for example in _load_examples():
        if example.get("archetype") != archetype:
            continue
        if output_mode not in example.get("allowed_output_modes", []):
            continue

        example_risks = set(example.get("risk_tags", []))
        score = len(requested_risks & example_risks) * 10
        if example.get("primary_output_mode") == output_mode:
            score += 3

        scored_examples.append((score, str(example.get("id", "")), example))

    scored_examples.sort(key=lambda item: (-item[0], item[1]))
    return tuple(example for _, _, example in scored_examples[:2])


@lru_cache(maxsize=1)
def _load_examples() -> tuple[dict[str, Any], ...]:
    examples_by_id: dict[str, dict[str, Any]] = {}
    for path in sorted(_EXAMPLES_DIR.glob("*.json")):
        dataset = _load_json(path)
        for example in dataset.get("examples", []):
            example_id = str(example.get("id", "")).strip()
            if not example_id or example_id in examples_by_id:
                continue
            examples_by_id[example_id] = example
    return tuple(examples_by_id.values())


def _load_archetype_template(archetype: str) -> dict[str, Any] | None:
    path = _ARCHETYPE_TEMPLATE_DIR / f"{archetype}.json"
    if not path.exists():
        return None
    return _load_json(path)


def _load_format_rules(output_mode: str, risk_tags: tuple[str, ...]) -> dict[str, Any] | None:
    templates = _load_json(_TEMPLATES_PATH)
    output_mode_config = templates.get("output_modes", {}).get(output_mode)
    if not isinstance(output_mode_config, dict):
        return None

    risk_hints = []
    known_hints = templates.get("risk_hints", {})
    for risk_tag in risk_tags:
        hint = known_hints.get(risk_tag)
        if hint:
            risk_hints.append(hint)

    return {
        "output_mode": output_mode,
        "expected_result_format": output_mode_config["expected_result_format"],
        "rules": [*templates.get("common_rules", []), *output_mode_config.get("rules", [])],
        "risk_hints": risk_hints,
    }


@lru_cache(maxsize=None)
def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)
