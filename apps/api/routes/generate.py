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
    log_event("generate_requested", path="/generate")
    response = GenerateResponse.model_validate(
        generation_service.generate(
            task_text=request.task_text,
            provided_context=request.provided_context,
        )
    )
    log_event(
        "generate_completed",
        path="/generate",
        validation_status=response.validation_status,
    )
    return response
