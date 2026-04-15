from typing import Literal

from pydantic import BaseModel, Field


class RuntimeOptionsRequest(BaseModel):
    num_ctx: int = Field(gt=0)
    num_predict: int = Field(gt=0)
    batch: int = Field(gt=0)
    temperature: float = Field(default=0.7, ge=0)
    top_p: float = Field(default=0.8, ge=0)
    top_k: int = Field(default=20, gt=0)
    min_p: float = Field(default=0.0, ge=0)
    presence_penalty: float = Field(default=1.5, ge=0)
    repeat_penalty: float = Field(default=1.0, ge=0)
    num_gpu: int | None = None


class ClarificationOption(BaseModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = ""


class ClarificationQuestion(BaseModel):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    options: list[ClarificationOption] = Field(default_factory=list)
    default_option_id: str | None = None


class UserClarification(BaseModel):
    question_id: str = Field(min_length=1)
    option_id: str = Field(min_length=1)
    free_text: str | None = None


class AssistedRepairOption(BaseModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    effect: str = Field(min_length=1)


class AssistedRepairRequest(BaseModel):
    summary: str = Field(min_length=1)
    failure_classes: list[str] = Field(default_factory=list)
    options: list[AssistedRepairOption] = Field(default_factory=list)
    latest_candidate: str


class _BaseGenerationRequest(BaseModel):
    task_text: str = Field(min_length=1)
    provided_context: str | None = None
    archetype: str | None = None
    output_mode: str | None = None
    input_roots: list[str] | None = None
    risk_tags: list[str] | None = None
    debug: bool = False
    mode: str = "release"
    model: str | None = None
    runtime_options: RuntimeOptionsRequest | None = None
    allow_cloud_model: bool = False
    language: Literal["ru", "en"] = "ru"


class PlanRequest(_BaseGenerationRequest):
    pass


class GenerateRequest(_BaseGenerationRequest):
    clarifications: list[UserClarification] | None = None
    feedback_text: str | None = None
    previous_candidate: str | None = None
    assisted_repair_option_id: str | None = None
    repair_budget: int = Field(default=2, gt=0)


class PlanResponse(BaseModel):
    task_spec: dict[str, object]
    clarification_required: bool
    questions: list[ClarificationQuestion] = Field(default_factory=list)
    trace: list[str]
    debug: dict[str, object] | None = None


class GenerateResponse(BaseModel):
    code: str
    validation_status: str
    stop_reason: str
    trace: list[str]
    validator_report: dict[str, object] | None = None
    critic_report: dict[str, object] | None = None
    repair_count: int = 0
    clarification_count: int = 0
    assisted_repair_request: AssistedRepairRequest | None = None
    output_mode: str | None = None
    archetype: str | None = None
    debug: dict[str, object] | None = None
