import sys
from pathlib import Path

from adapters.model import OllamaModelAdapter
from runtime_policy import RELEASE_MODE, RuntimeOptions, normalize_mode

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from packages.orchestrator.domain_adapter import build_domain_prompt_package  # noqa: E402
from packages.orchestrator.repair_loop import run_quality_loop  # noqa: E402
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
        return run_quality_loop(model_adapter, prompt_package, debug=debug).to_dict()

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
