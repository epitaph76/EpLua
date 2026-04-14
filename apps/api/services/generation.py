import sys
from pathlib import Path

from adapters.model import OllamaModelAdapter
from runtime_policy import RELEASE_MODE, RuntimeOptions, normalize_mode

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from packages.orchestrator.prompter import LOWCODE_LUA_EXPECTED_RESULT_FORMAT, build_lowcode_generator_prompt  # noqa: E402
from packages.shared.language import DEFAULT_LANGUAGE  # noqa: E402


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
        mode: str = RELEASE_MODE,
        model: str | None = None,
        runtime_options: dict[str, int | float] | RuntimeOptions | None = None,
        allow_cloud_model: bool = False,
        language: str = DEFAULT_LANGUAGE,
    ) -> dict[str, object]:
        model_adapter = self._adapter_for_request(
            mode=mode,
            model=model,
            runtime_options=runtime_options,
            allow_cloud_model=allow_cloud_model,
        )
        prompt = build_lowcode_generator_prompt(task_text, provided_context)
        code = model_adapter.generate_from_prompt(prompt)
        return {
            "code": code,
            "validation_status": "not_run",
            "stop_reason": "not_run",
            "trace": [
                "request_received",
                "generation",
                "response_ready",
            ],
            "validator_report": None,
            "critic_report": None,
            "repair_count": 0,
            "clarification_count": 0,
            "output_mode": output_mode,
            "archetype": archetype,
            "debug": self._build_debug_payload(prompt, code) if debug else None,
        }

    def _build_debug_payload(
        self,
        prompt: str,
        raw_response: str,
    ) -> dict[str, object]:
        return {
            "prompt_package": {
                "prompt": prompt,
                "expected_result_format": LOWCODE_LUA_EXPECTED_RESULT_FORMAT,
            },
            "pipeline_layers": [
                {
                    "stage": "generator",
                    "kind": "llm_prompt",
                    "status": "completed",
                    "agent": "generator",
                },
            ],
            "agent_layer_calls": [],
            "model_calls": [
                {
                    "phase": "generation",
                    "agent": "generator",
                    "prompt": prompt,
                    "raw_response": raw_response,
                }
            ],
            "validation_passes": [],
        }

    def _adapter_for_request(
        self,
        *,
        mode: str,
        model: str | None,
        runtime_options: dict[str, int | float] | RuntimeOptions | None,
        allow_cloud_model: bool,
    ) -> OllamaModelAdapter:
        normalized_mode = normalize_mode(mode)
        if (
            normalized_mode == RELEASE_MODE
            and model is None
            and runtime_options is None
            and not allow_cloud_model
        ):
            return self._model_adapter

        options = runtime_options if isinstance(runtime_options, RuntimeOptions) else RuntimeOptions.from_mapping(runtime_options)
        return self._model_adapter.with_overrides(
            model=model,
            runtime_options=options,
            mode=normalized_mode,
            allow_cloud_model=allow_cloud_model,
        )
