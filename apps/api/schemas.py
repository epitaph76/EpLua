from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    task_text: str = Field(min_length=1)
    provided_context: str | None = None
    archetype: str | None = None
    output_mode: str | None = None
    input_roots: list[str] | None = None
    risk_tags: list[str] | None = None
    debug: bool = False


class GenerateResponse(BaseModel):
    code: str
    validation_status: str
    trace: list[str]
    validator_report: dict[str, object] | None = None
    critic_report: dict[str, object] | None = None
    repair_count: int = 0
    clarification_count: int = 0
    output_mode: str | None = None
    archetype: str | None = None
    debug: dict[str, object] | None = None
