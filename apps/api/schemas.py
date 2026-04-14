from typing import Literal

from pydantic import BaseModel, Field


class RuntimeOptionsRequest(BaseModel):
    num_ctx: int = Field(gt=0)
    num_predict: int = Field(gt=0)
    batch: int = Field(gt=0)
    temperature: float = Field(default=0.8, ge=0)
    num_gpu: int | None = None


class GenerateRequest(BaseModel):
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
    repair_budget: int = Field(default=2, gt=0)


class GenerateResponse(BaseModel):
    code: str
    validation_status: str
    stop_reason: str
    trace: list[str]
    validator_report: dict[str, object] | None = None
    critic_report: dict[str, object] | None = None
    repair_count: int = 0
    clarification_count: int = 0
    output_mode: str | None = None
    archetype: str | None = None
    debug: dict[str, object] | None = None
