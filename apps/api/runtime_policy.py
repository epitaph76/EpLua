from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping

from errors import ApiError


DEFAULT_MODEL_TAG = "qwen3.5:9b"
DEFAULT_NUM_CTX = 4096
DEFAULT_NUM_PREDICT = 256
DEFAULT_BATCH = 1
DEFAULT_PARALLEL = 1
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TOP_P = 0.8
DEFAULT_TOP_K = 20
DEFAULT_MIN_P = 0.0
DEFAULT_PRESENCE_PENALTY = 1.5
DEFAULT_REPEAT_PENALTY = 1.0
RELEASE_NUM_GPU = -1
RELEASE_MODE = "release"
RELEASE_SLIM_MODE = "releaseSlim"
DEBUG_MODE = "debug"
_RELEASE_SLIM_ALIASES = {"releaseslim", "release-slim", "release_slim"}
_CLOUD_MODEL_PATTERN = re.compile(r"(^|[:_-])cloud($|[:_-])|-cloud$")


@dataclass(frozen=True)
class RuntimeOptions:
    num_ctx: int = DEFAULT_NUM_CTX
    num_predict: int = DEFAULT_NUM_PREDICT
    batch: int = DEFAULT_BATCH
    temperature: float = DEFAULT_TEMPERATURE
    top_p: float = DEFAULT_TOP_P
    top_k: int = DEFAULT_TOP_K
    min_p: float = DEFAULT_MIN_P
    presence_penalty: float = DEFAULT_PRESENCE_PENALTY
    repeat_penalty: float = DEFAULT_REPEAT_PENALTY
    num_gpu: int | None = None

    @classmethod
    def from_env(cls) -> "RuntimeOptions":
        return cls(
            num_ctx=_positive_int_from_env("OLLAMA_NUM_CTX", DEFAULT_NUM_CTX),
            num_predict=_positive_int_from_env("OLLAMA_NUM_PREDICT", DEFAULT_NUM_PREDICT),
            batch=_positive_int_from_env("OLLAMA_BATCH", DEFAULT_BATCH),
            temperature=_non_negative_float_from_env("OLLAMA_TEMPERATURE", DEFAULT_TEMPERATURE),
            top_p=_non_negative_float_from_env("OLLAMA_TOP_P", DEFAULT_TOP_P),
            top_k=_positive_int_from_env("OLLAMA_TOP_K", DEFAULT_TOP_K),
            min_p=_non_negative_float_from_env("OLLAMA_MIN_P", DEFAULT_MIN_P),
            presence_penalty=_non_negative_float_from_env("OLLAMA_PRESENCE_PENALTY", DEFAULT_PRESENCE_PENALTY),
            repeat_penalty=_non_negative_float_from_env("OLLAMA_REPEAT_PENALTY", DEFAULT_REPEAT_PENALTY),
        )

    @classmethod
    def release_defaults(cls) -> "RuntimeOptions":
        return cls(
            num_ctx=DEFAULT_NUM_CTX,
            num_predict=DEFAULT_NUM_PREDICT,
            batch=DEFAULT_BATCH,
            temperature=DEFAULT_TEMPERATURE,
            top_p=DEFAULT_TOP_P,
            top_k=DEFAULT_TOP_K,
            min_p=DEFAULT_MIN_P,
            presence_penalty=DEFAULT_PRESENCE_PENALTY,
            repeat_penalty=DEFAULT_REPEAT_PENALTY,
            num_gpu=RELEASE_NUM_GPU,
        )

    @classmethod
    def release_slim_defaults(cls) -> "RuntimeOptions":
        return cls(
            num_ctx=DEFAULT_NUM_CTX,
            num_predict=DEFAULT_NUM_PREDICT,
            batch=DEFAULT_BATCH,
            temperature=DEFAULT_TEMPERATURE,
            top_p=DEFAULT_TOP_P,
            top_k=DEFAULT_TOP_K,
            min_p=DEFAULT_MIN_P,
            presence_penalty=DEFAULT_PRESENCE_PENALTY,
            repeat_penalty=DEFAULT_REPEAT_PENALTY,
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
            top_p=_non_negative_float(payload.get("top_p", DEFAULT_TOP_P), "top_p"),
            top_k=_positive_int(payload.get("top_k", DEFAULT_TOP_K), "top_k"),
            min_p=_non_negative_float(payload.get("min_p", DEFAULT_MIN_P), "min_p"),
            presence_penalty=_non_negative_float(payload.get("presence_penalty", DEFAULT_PRESENCE_PENALTY), "presence_penalty"),
            repeat_penalty=_non_negative_float(payload.get("repeat_penalty", DEFAULT_REPEAT_PENALTY), "repeat_penalty"),
            num_gpu=_optional_int(payload.get("num_gpu"), "num_gpu"),
        )

    def to_ollama_options(self) -> dict[str, int | float]:
        options: dict[str, int | float] = {
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
            "batch": self.batch,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "min_p": self.min_p,
            "presence_penalty": self.presence_penalty,
            "repeat_penalty": self.repeat_penalty,
        }
        if self.num_gpu is not None:
            options["num_gpu"] = self.num_gpu
        return options


def effective_parallel() -> int:
    return _positive_int_from_env("OLLAMA_PARALLEL", DEFAULT_PARALLEL)


def normalize_mode(mode: str | None) -> str:
    raw_mode = (mode or RELEASE_MODE).strip()
    normalized = raw_mode.lower()
    if normalized == RELEASE_MODE:
        return RELEASE_MODE
    if normalized == DEBUG_MODE:
        return DEBUG_MODE
    if normalized in _RELEASE_SLIM_ALIASES:
        return RELEASE_SLIM_MODE
    if raw_mode == RELEASE_SLIM_MODE:
        return RELEASE_SLIM_MODE
    raise ApiError(
        status_code=422,
        code="invalid_runtime_mode",
        message="Runtime mode must be release, releaseSlim, or debug.",
    )


def is_release_like_mode(mode: str | None) -> bool:
    return normalize_mode(mode) in {RELEASE_MODE, RELEASE_SLIM_MODE}


def default_runtime_options_for_mode(mode: str | None) -> RuntimeOptions:
    normalized_mode = normalize_mode(mode)
    if normalized_mode == RELEASE_MODE:
        return RuntimeOptions.release_defaults()
    if normalized_mode == RELEASE_SLIM_MODE:
        return RuntimeOptions.release_slim_defaults()
    return RuntimeOptions.from_env()


def mode_allows_runtime_overrides(mode: str | None) -> bool:
    return not is_release_like_mode(mode)


def mode_label(mode: str | None) -> str:
    return normalize_mode(mode)


def is_debug_mode(mode: str | None) -> bool:
    return normalize_mode(mode) == DEBUG_MODE


def mode_uses_release_spinner(mode: str | None) -> bool:
    return normalize_mode(mode) in {RELEASE_MODE, RELEASE_SLIM_MODE}


def mode_supports_cloud_override(mode: str | None) -> bool:
    return normalize_mode(mode) == DEBUG_MODE


def mode_shows_compact_status(mode: str | None) -> bool:
    return normalize_mode(mode) == RELEASE_SLIM_MODE


def is_cloud_model_tag(model: str) -> bool:
    return bool(_CLOUD_MODEL_PATTERN.search(model))


def enforce_model_policy(
    model: str,
    *,
    mode: str | None = None,
    allow_cloud_model: bool = False,
) -> None:
    normalized_mode = normalize_mode(mode)
    if is_release_like_mode(normalized_mode) and allow_cloud_model:
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
        message="Cloud Ollama model tags are not allowed in release or releaseSlim mode.",
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


def _optional_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise ApiError(
            status_code=422,
            code="invalid_runtime_options",
            message=f"{name} must be an integer.",
        ) from exc


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
