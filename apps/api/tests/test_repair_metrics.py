import json
import sys
from pathlib import Path

from services.generation import GenerationService

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from packages.orchestrator.domain_adapter import build_domain_prompt_package  # noqa: E402
from packages.validators.core import run_validation_pipeline  # noqa: E402


class ScriptedModelAdapter:
    def __init__(self, responses_by_case: dict[str, list[str]]) -> None:
        self._responses_by_case = {case_id: list(responses) for case_id, responses in responses_by_case.items()}
        self._current_case_id: str | None = None

    def start_case(self, case_id: str) -> None:
        self._current_case_id = case_id

    def generate_from_prompt(self, prompt: str) -> str:
        if self._current_case_id is None:
            raise AssertionError("Case id must be selected before generating a prompt response.")
        responses = self._responses_by_case[self._current_case_id]
        if not responses:
            raise AssertionError(f"No scripted responses left for case {self._current_case_id}.")
        return responses.pop(0)


def test_initial_regression_pack_has_non_negative_repair_uplift() -> None:
    public_cases = _load_json(REPO_ROOT / "benchmark" / "public_cases.json")["cases"]
    pack_cases = _load_json(REPO_ROOT / "benchmark" / "initial_regression_pack.json")["cases"]
    public_case_map = {case["id"]: case for case in public_cases}
    scripted_responses = _build_scripted_responses(public_case_map, pack_cases)
    model_adapter = ScriptedModelAdapter(scripted_responses)
    service = GenerationService(model_adapter=model_adapter)

    baseline_successes = 0
    final_successes = 0

    for pack_case in pack_cases:
        public_case = public_case_map[pack_case["case_id"]]
        context = (
            json.dumps(public_case["context"], ensure_ascii=False)
            if public_case["context"] is not None
            else None
        )
        prompt_package = build_domain_prompt_package(
            public_case["prompt"],
            context,
            archetype=public_case["archetype"],
            output_mode=public_case["primary_output_mode"],
            input_roots=public_case["input_roots"],
            risk_tags=public_case["risk_tags"],
        )

        initial_candidate = scripted_responses[pack_case["case_id"]][0]
        _, format_report, rule_report = run_validation_pipeline(
            initial_candidate,
            output_mode=prompt_package.output_mode,
            allowed_data_roots=prompt_package.allowed_data_roots,
            forbidden_patterns=prompt_package.forbidden_patterns,
            risk_tags=prompt_package.risk_tags,
            archetype=prompt_package.archetype,
        )
        if format_report.status == "pass" and rule_report.status == "pass":
            baseline_successes += 1

        model_adapter.start_case(pack_case["case_id"])
        result = service.generate(
            task_text=public_case["prompt"],
            provided_context=context,
            archetype=public_case["archetype"],
            output_mode=public_case["primary_output_mode"],
            input_roots=public_case["input_roots"],
            risk_tags=public_case["risk_tags"],
        )
        if result["validation_status"] in {"passed", "repaired"}:
            final_successes += 1

    assert final_successes - baseline_successes >= 0
    assert final_successes > baseline_successes


def _build_scripted_responses(
    public_case_map: dict[str, dict[str, object]],
    pack_cases: list[dict[str, object]],
) -> dict[str, list[str]]:
    scripted_responses: dict[str, list[str]] = {}
    for pack_case in pack_cases:
        public_case = public_case_map[pack_case["case_id"]]
        expected_output = public_case["expected_outputs"][public_case["primary_output_mode"]]
        if isinstance(expected_output, dict):
            valid_candidate = json.dumps(expected_output, ensure_ascii=False, separators=(",", ":"))
        else:
            valid_candidate = str(expected_output)

        if pack_case["case_id"] == "case-01-last-array-item":
            scripted_responses[pack_case["case_id"]] = [f"```lua\n{valid_candidate}\n```", valid_candidate]
            continue

        scripted_responses[pack_case["case_id"]] = [valid_candidate]
    return scripted_responses


def _load_json(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)
