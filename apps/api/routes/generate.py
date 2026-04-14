from fastapi import APIRouter, Depends

from schemas import GenerateRequest, GenerateResponse
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
    response = GenerateResponse.model_validate(
        generation_service.generate(
            task_text=request.task_text,
            provided_context=request.provided_context,
            archetype=request.archetype,
            output_mode=request.output_mode,
            input_roots=request.input_roots,
            risk_tags=request.risk_tags,
            debug=request.debug,
            mode=request.mode,
            model=request.model,
            runtime_options=request.runtime_options.model_dump() if request.runtime_options else None,
            allow_cloud_model=request.allow_cloud_model,
            language=request.language,
        )
    )
    log_event(
        "generate_completed",
        path="/generate",
        validation_status=response.validation_status,
        debug=request.debug,
    )
    return response
