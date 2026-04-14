from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping

from errors import ApiError


DEFAULT_MODEL_TAG = "qwen2.5-coder:3b"
DEFAULT_NUM_CTX = 4096
DEFAULT_NUM_PREDICT = 256
DEFAULT_BATCH = 1
DEFAULT_PARALLEL = 1
DEFAULT_TEMPERATURE = 0.8
RELEASE_MODE = "release"
DEBUG_MODE = "debug"
_CLOUD_MODEL_PATTERN = re.compile(r"(^|[:_-])cloud($|[:_-])|-cloud$")


@dataclass(frozen=True)
class RuntimeOptions:
    num_ctx: int = DEFAULT_NUM_CTX
    num_predict: int = DEFAULT_NUM_PREDICT
    batch: int = DEFAULT_BATCH
    temperature: float = DEFAULT_TEMPERATURE

    @classmethod
    def from_env(cls) -> "RuntimeOptions":
        return cls(
            num_ctx=_positive_int_from_env("OLLAMA_NUM_CTX", DEFAULT_NUM_CTX),
            num_predict=_positive_int_from_env("OLLAMA_NUM_PREDICT", DEFAULT_NUM_PREDICT),
            batch=_positive_int_from_env("OLLAMA_BATCH", DEFAULT_BATCH),
            temperature=_non_negative_float_from_env("OLLAMA_TEMPERATURE", DEFAULT_TEMPERATURE),
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object] | None) -> "RuntimeOptions":
        if payload is None:
            return cls.from_env()
        return cls(
            num_ctx=_positive_int(payload.get("num_ctx"), "num_ctx"),
            num_predict=_positive_int(payload.get("num_predict"), "num_predict"),
            batch=_positive_int(payload.get("batch"), "batch"),
            temperature=_non_negative_float(payload.get("temperature", DEFAULT_TEMPERATURE), "temperature"),
        )

    def to_ollama_options(self) -> dict[str, int | float]:
        return {
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
            "batch": self.batch,
            "temperature": self.temperature,
        }


def effective_parallel() -> int:
    return _positive_int_from_env("OLLAMA_PARALLEL", DEFAULT_PARALLEL)


def normalize_mode(mode: str | None) -> str:
    normalized = (mode or RELEASE_MODE).strip().lower()
    if normalized not in {RELEASE_MODE, DEBUG_MODE}:
        raise ApiError(
            status_code=422,
            code="invalid_runtime_mode",
            message="Runtime mode must be either release or debug.",
        )
    return normalized


def is_cloud_model_tag(model: str) -> bool:
    return bool(_CLOUD_MODEL_PATTERN.search(model))


def enforce_model_policy(
    model: str,
    *,
    mode: str | None = None,
    allow_cloud_model: bool = False,
) -> None:
    normalized_mode = normalize_mode(mode)
    if normalized_mode == RELEASE_MODE and allow_cloud_model:
        raise ApiError(
            status_code=422,
            code="cloud_model_not_allowed",
            message="--allow-cloud-model is only available in debug mode.",
        )
    if not is_cloud_model_tag(model):
        return
    if normalized_mode == DEBUG_MODE and allow_cloud_model:
        return
    if normalized_mode == DEBUG_MODE:
        raise ApiError(
            status_code=422,
            code="cloud_model_not_allowed",
            message="Cloud Ollama model tags in debug mode require --allow-cloud-model.",
        )
    raise ApiError(
        status_code=422,
        code="cloud_model_not_allowed",
        message="Cloud Ollama model tags are not allowed in release mode.",
    )


def _positive_int_from_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return _positive_int(raw_value, name)
    except ApiError:
        return default


def _non_negative_float_from_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return _non_negative_float(raw_value, name)
    except ApiError:
        return default


def _positive_int(value: object, name: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ApiError(
            status_code=422,
            code="invalid_runtime_options",
            message=f"{name} must be a positive integer.",
        ) from exc
    if parsed <= 0:
        raise ApiError(
            status_code=422,
            code="invalid_runtime_options",
            message=f"{name} must be a positive integer.",
        )
    return parsed


def _non_negative_float(value: object, name: str) -> float:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ApiError(
            status_code=422,
            code="invalid_runtime_options",
            message=f"{name} must be a non-negative number.",
        ) from exc
    if parsed < 0:
        raise ApiError(
            status_code=422,
            code="invalid_runtime_options",
            message=f"{name} must be a non-negative number.",
        )
    return parsed
