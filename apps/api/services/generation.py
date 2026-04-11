import sys
from pathlib import Path

from adapters.model import OllamaModelAdapter

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from packages.orchestrator.domain_adapter import build_domain_prompt_package  # noqa: E402
from packages.orchestrator.repair_loop import run_quality_loop  # noqa: E402


class GenerationService:
    def __init__(self, model_adapter: OllamaModelAdapter | None = None) -> None:
        self._model_adapter = model_adapter or OllamaModelAdapter()

    def generate(
        self,
        task_text: str,
        provided_context: str | None = None,
        *,
        archetype: str | None = None,
        output_mode: str | None = None,
        input_roots: list[str] | None = None,
        risk_tags: list[str] | None = None,
        debug: bool = False,
    ) -> dict[str, object]:
        if archetype and output_mode:
            prompt_package = build_domain_prompt_package(
                task_text,
                provided_context,
                archetype=archetype,
                output_mode=output_mode,
                input_roots=input_roots,
                risk_tags=risk_tags,
            )
            return run_quality_loop(self._model_adapter, prompt_package, debug=debug).to_dict()

        if debug:
            prompt_parts = [task_text]
            if provided_context:
                prompt_parts.append(provided_context)
            prompt = "\n\n".join(prompt_parts)
            code = self._model_adapter.generate_from_prompt(prompt)
            debug_payload: dict[str, object] | None = {
                "prompt_package": None,
                "model_calls": [
                    {
                        "phase": "generation",
                        "prompt": prompt,
                        "raw_response": code,
                    }
                ],
                "validation_passes": [],
            }
        else:
            code = self._model_adapter.generate(task_text, provided_context)
            debug_payload = None
        return {
            "code": code,
            "validation_status": "not_run",
            "trace": ["request_received", "model_invoked", "response_ready"],
            "validator_report": None,
            "critic_report": None,
            "repair_count": 0,
            "clarification_count": 0,
            "output_mode": None,
            "archetype": None,
            "debug": debug_payload,
        }
