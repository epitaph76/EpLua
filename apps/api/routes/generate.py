import json
import queue
import threading
from collections.abc import Callable, Iterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from errors import ApiError
from runtime_policy import default_runtime_options_for_mode, mode_allows_runtime_overrides
from schemas import GenerateRequest, GenerateResponse, PlanRequest, PlanResponse
from services.generation import GenerationService
from structured_logging import log_event


router = APIRouter()


def get_generation_service() -> GenerationService:
    return GenerationService()


@router.post("/generate", response_model=GenerateResponse)
def generate(
    request: GenerateRequest,
    generation_service: GenerationService = Depends(get_generation_service),
) -> GenerateResponse:
    log_event("generate_requested", path="/generate", debug=request.debug)
    response = GenerateResponse.model_validate(_run_generation(request, generation_service))
    log_event(
        "generate_completed",
        path="/generate",
        validation_status=response.validation_status,
        debug=request.debug,
    )
    return response


@router.post("/plan", response_model=PlanResponse)
def plan(
    request: PlanRequest,
    generation_service: GenerationService = Depends(get_generation_service),
) -> PlanResponse:
    log_event("plan_requested", path="/plan", debug=request.debug)
    response = PlanResponse.model_validate(_run_plan(request, generation_service))
    log_event(
        "plan_completed",
        path="/plan",
        clarification_required=response.clarification_required,
        debug=request.debug,
    )
    return response


@router.post("/generate/progress")
def generate_progress(
    request: GenerateRequest,
    generation_service: GenerationService = Depends(get_generation_service),
) -> StreamingResponse:
    log_event("generate_requested", path="/generate/progress", debug=request.debug)
    events: queue.Queue[dict[str, object] | None] = queue.Queue()
    progress_index = 0

    def emit_progress(stage: str) -> None:
        nonlocal progress_index
        progress_index += 1
        events.put({"type": "progress", "stage": stage, "index": progress_index})

    def run_worker() -> None:
        try:
            response = GenerateResponse.model_validate(
                _run_generation(request, generation_service, progress_callback=emit_progress)
            )
            events.put({"type": "final", "payload": response.model_dump(mode="json")})
            log_event(
                "generate_completed",
                path="/generate/progress",
                validation_status=response.validation_status,
                debug=request.debug,
            )
        except ApiError as exc:
            events.put(
                {
                    "type": "error",
                    "status_code": exc.status_code,
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "details": exc.details,
                    },
                }
            )
        except Exception as exc:  # pragma: no cover - defensive stream boundary
            events.put(
                {
                    "type": "error",
                    "status_code": 500,
                    "error": {
                        "code": "internal_error",
                        "message": str(exc),
                        "details": [],
                    },
                }
            )
        finally:
            events.put(None)

    worker = threading.Thread(target=run_worker, daemon=True)
    worker.start()

    def iter_events() -> Iterator[str]:
        while True:
            event = events.get()
            if event is None:
                break
            yield json.dumps(event, ensure_ascii=False) + "\n"
        worker.join(timeout=0.2)

    return StreamingResponse(iter_events(), media_type="application/x-ndjson")


def _run_generation(
    request: GenerateRequest,
    generation_service: GenerationService,
    *,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, object]:
    kwargs = _request_kwargs(request)
    kwargs["repair_budget"] = request.repair_budget
    if progress_callback is not None:
        kwargs["progress_callback"] = progress_callback
    return generation_service.generate(**kwargs)


def _run_plan(
    request: PlanRequest,
    generation_service: GenerationService,
) -> dict[str, object]:
    return generation_service.plan(**_request_kwargs(request))


def _request_kwargs(request: GenerateRequest | PlanRequest) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "task_text": request.task_text,
        "provided_context": request.provided_context,
        "archetype": request.archetype,
        "output_mode": request.output_mode,
        "input_roots": request.input_roots,
        "risk_tags": request.risk_tags,
        "debug": request.debug,
        "mode": request.mode,
        "model": request.model,
        "runtime_options": _runtime_options_payload(request),
        "allow_cloud_model": request.allow_cloud_model,
        "language": request.language,
    }
    clarifications = getattr(request, "clarifications", None)
    if clarifications is not None:
        kwargs["clarifications"] = [clarification.model_dump() for clarification in clarifications]
    feedback_text = getattr(request, "feedback_text", None)
    if feedback_text is not None:
        kwargs["feedback_text"] = feedback_text
    previous_candidate = getattr(request, "previous_candidate", None)
    if previous_candidate is not None:
        kwargs["previous_candidate"] = previous_candidate
    assisted_repair_option_id = getattr(request, "assisted_repair_option_id", None)
    if assisted_repair_option_id is not None:
        kwargs["assisted_repair_option_id"] = assisted_repair_option_id
    return kwargs


def _runtime_options_payload(request: GenerateRequest | PlanRequest) -> dict[str, int | float] | None:
    if request.runtime_options:
        return request.runtime_options.model_dump(exclude_none=True)
    if not mode_allows_runtime_overrides(request.mode):
        return default_runtime_options_for_mode(request.mode).to_ollama_options()
    return None
