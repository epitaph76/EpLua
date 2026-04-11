from adapters.model import OllamaModelAdapter


class GenerationService:
    def __init__(self, model_adapter: OllamaModelAdapter | None = None) -> None:
        self._model_adapter = model_adapter or OllamaModelAdapter()

    def generate(self, task_text: str, provided_context: str | None = None) -> dict[str, object]:
        code = self._model_adapter.generate(task_text, provided_context)
        return {
            "code": code,
            "validation_status": "not_run",
            "trace": ["request_received", "model_invoked", "response_ready"],
        }
