import sys
from pathlib import Path

from adapters.model import OllamaModelAdapter
from runtime_policy import RELEASE_MODE, RuntimeOptions, normalize_mode

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from packages.orchestrator.domain_adapter import DomainPromptPackage, build_domain_prompt_package  # noqa: E402
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
        runtime_options: dict[str, int] | RuntimeOptions | None = None,
        allow_cloud_model: bool = False,
        language: str = DEFAULT_LANGUAGE,
    ) -> dict[str, object]:
        model_adapter = self._adapter_for_request(
            mode=mode,
            model=model,
            runtime_options=runtime_options,
            allow_cloud_model=allow_cloud_model,
        )
        agent_runner = getattr(model_adapter, "generate_from_agent", None)
        prompt_package = build_domain_prompt_package(
            task_text,
            provided_context,
            archetype=archetype,
            output_mode=output_mode,
            input_roots=input_roots,
            risk_tags=risk_tags,
            language=language,
            agent_runner=agent_runner if callable(agent_runner) else None,
        )
        code = self._generate_from_prompt_package(model_adapter, prompt_package)
        return {
            "code": code,
            "validation_status": "not_run",
            "stop_reason": "not_run",
            "trace": [
                "request_received",
                "planner",
                "prompter",
                "generation",
                "response_ready",
            ],
            "validator_report": None,
            "critic_report": None,
            "repair_count": 0,
            "clarification_count": 0,
            "output_mode": prompt_package.output_mode,
            "archetype": prompt_package.archetype,
            "debug": self._build_debug_payload(prompt_package, code) if debug else None,
        }

    def _generate_from_prompt_package(
        self,
        model_adapter: OllamaModelAdapter,
        prompt_package: DomainPromptPackage,
    ) -> str:
        generator = getattr(model_adapter, "generate_from_agent", None)
        if callable(generator):
            return str(generator(prompt_package.agent_prompt))
        return model_adapter.generate_from_prompt(prompt_package.prompt)

    def _build_debug_payload(
        self,
        prompt_package: DomainPromptPackage,
        raw_response: str,
    ) -> dict[str, object]:
        return {
            "prompt_package": {
                "prompt": prompt_package.prompt,
                "archetype": prompt_package.archetype,
                "output_mode": prompt_package.output_mode,
                "expected_result_format": prompt_package.expected_result_format,
                "allowed_data_roots": list(prompt_package.allowed_data_roots),
                "forbidden_patterns": list(prompt_package.forbidden_patterns),
                "risk_tags": list(prompt_package.risk_tags),
                "task_intents": list(prompt_package.task_intents),
                "clarification_required": prompt_package.clarification_required,
                "task_spec": prompt_package.task_spec.to_dict(),
                "planner_result": prompt_package.planner_result.to_debug_dict(),
                "prompt_builder_result": prompt_package.prompt_builder_result.to_debug_dict(),
                "agent_prompt": {
                    "agent": prompt_package.agent_prompt.agent_name,
                    "messages": prompt_package.agent_prompt.to_messages_payload(),
                },
            },
            "pipeline_layers": [
                {
                    "stage": "input_normalization",
                    "kind": "deterministic",
                    "status": "completed",
                },
                {
                    "stage": "planner",
                    "kind": "agent_layer",
                    "status": "completed",
                    "details": prompt_package.planner_result.to_debug_dict(),
                },
                {
                    "stage": "prompter",
                    "kind": "agent_layer",
                    "status": "completed",
                    "details": prompt_package.prompt_builder_result.to_debug_dict(),
                },
                {
                    "stage": "generator",
                    "kind": "llm_agent",
                    "status": "completed",
                    "agent": prompt_package.agent_prompt.agent_name,
                },
            ],
            "agent_layer_calls": list(prompt_package.agent_layer_calls),
            "model_calls": [
                {
                    "phase": "generation",
                    "agent": prompt_package.agent_prompt.agent_name,
                    "prompt": prompt_package.prompt,
                    "messages": prompt_package.agent_prompt.to_messages_payload(),
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
        runtime_options: dict[str, int] | RuntimeOptions | None,
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
