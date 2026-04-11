from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    task_text: str = Field(min_length=1)
    provided_context: str | None = None


class GenerateResponse(BaseModel):
    code: str
    validation_status: str
    trace: list[str]
