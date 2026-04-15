import json
import logging

from fastapi.testclient import TestClient

from errors import ApiError
from main import app
from routes.generate import get_generation_service


class FakeGenerationService:
    def generate(
        self,
        task_text: str,
        provided_context: str | None = None,
        *,
        archetype: str | None = None,
        output_mode: str | None = None,
        input_roots: list[str] | None = None,
        risk_tags: list[str] | None = None,
        debug: bool = False,
        mode: str = "release",
        model: str | None = None,
        runtime_options: dict[str, int | float] | None = None,
        allow_cloud_model: bool = False,
        language: str = "ru",
        repair_budget: int = 2,
    ) -> dict[str, object]:
        assert task_text == "make a LocalScript"
        assert provided_context == "inventory payload"
        assert archetype is None
        assert output_mode is None
        assert input_roots is None
        assert risk_tags is None
        assert debug is False
        assert mode == "release"
        assert model is None
        assert runtime_options == {"num_ctx": 4096, "num_predict": 256, "batch": 1, "temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5, "repeat_penalty": 1.0, "num_gpu": -1}
        assert allow_cloud_model is False
        assert language == "ru"
        assert repair_budget == 2

        return {
            "code": "print('ok')",
            "validation_status": "not_run",
            "stop_reason": "not_run",
            "trace": ["request_received", "response_ready"],
        }


class FailingGenerationService:
    def generate(
        self,
        task_text: str,
        provided_context: str | None = None,
        *,
        archetype: str | None = None,
        output_mode: str | None = None,
        input_roots: list[str] | None = None,
        risk_tags: list[str] | None = None,
        debug: bool = False,
        mode: str = "release",
        model: str | None = None,
        runtime_options: dict[str, int] | None = None,
        allow_cloud_model: bool = False,
        language: str = "ru",
        repair_budget: int = 2,
    ) -> dict[str, object]:
        raise ApiError(status_code=502, code="model_error", message="Local model request failed.")


class QualityGenerationService:
    def generate(
        self,
        task_text: str,
        provided_context: str | None = None,
        *,
        archetype: str | None = None,
        output_mode: str | None = None,
        input_roots: list[str] | None = None,
        risk_tags: list[str] | None = None,
        debug: bool = False,
        mode: str = "release",
        model: str | None = None,
        runtime_options: dict[str, int] | None = None,
        allow_cloud_model: bool = False,
        language: str = "ru",
        repair_budget: int = 2,
    ) -> dict[str, object]:
        assert task_text == "make a LocalScript"
        assert provided_context == "inventory payload"
        assert archetype == "simple_extraction"
        assert output_mode == "raw_lua"
        assert input_roots == ["wf.vars.emails"]
        assert risk_tags == ["array_indexing", "empty_array"]
        assert debug is False
        assert language == "ru"
        assert repair_budget == 2

        return {
            "code": "return wf.vars.emails[#wf.vars.emails]",
            "validation_status": "passed",
            "stop_reason": "passed",
            "trace": [
                "request_received",
                "generation",
                "format_validation",
                "rule_validation",
                "finalize",
            ],
            "validator_report": {
                "status": "pass",
                "iterations": [],
            },
            "critic_report": None,
            "repair_count": 0,
            "clarification_count": 0,
            "output_mode": "raw_lua",
            "archetype": "simple_extraction",
            "debug": None,
        }


class DebugGenerationService:
    def generate(
        self,
        task_text: str,
        provided_context: str | None = None,
        *,
        archetype: str | None = None,
        output_mode: str | None = None,
        input_roots: list[str] | None = None,
        risk_tags: list[str] | None = None,
        debug: bool = False,
        mode: str = "release",
        model: str | None = None,
        runtime_options: dict[str, int] | None = None,
        allow_cloud_model: bool = False,
        language: str = "ru",
        repair_budget: int = 2,
    ) -> dict[str, object]:
        assert debug is True
        assert language == "ru"
        assert repair_budget == 2
        return {
            "code": "return wf.vars.emails[#wf.vars.emails]",
            "validation_status": "repaired",
            "stop_reason": "passed",
            "trace": [
                "request_received",
                "generation",
                "format_validation",
                "critic_step",
                "repair_generation",
                "format_validation",
                "rule_validation",
                "finalize",
            ],
            "validator_report": {
                "status": "pass",
                "iterations": [],
            },
            "critic_report": {
                "action": "repair",
                "failure_class": "markdown_fence",
            },
            "repair_count": 1,
            "clarification_count": 0,
            "output_mode": "raw_lua",
            "archetype": "simple_extraction",
            "debug": {
                "prompt_package": {"prompt": "PROMPT"},
                "model_calls": [{"phase": "generation", "prompt": "PROMPT", "raw_response": "```lua\nreturn x\n```"}],
                "validation_passes": [],
            },
        }


class RuntimePolicyGenerationService:
    def generate(
        self,
        task_text: str,
        provided_context: str | None = None,
        *,
        archetype: str | None = None,
        output_mode: str | None = None,
        input_roots: list[str] | None = None,
        risk_tags: list[str] | None = None,
        debug: bool = False,
        mode: str = "release",
        model: str | None = None,
        runtime_options: dict[str, int] | None = None,
        allow_cloud_model: bool = False,
        language: str = "ru",
        repair_budget: int = 2,
    ) -> dict[str, object]:
        assert task_text == "make a LocalScript"
        assert provided_context == "inventory payload"
        assert archetype is None
        assert output_mode is None
        assert input_roots is None
        assert risk_tags is None
        assert debug is True
        assert mode == "debug"
        assert model == "qwen3.5:9b"
        assert runtime_options == {"num_ctx": 2048, "num_predict": 128, "batch": 1, "temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5, "repeat_penalty": 1.0, "num_gpu": -1}
        assert allow_cloud_model is False
        assert language == "ru"
        assert repair_budget == 2
        return {
            "code": "print('debug')",
            "validation_status": "not_run",
            "stop_reason": "not_run",
            "trace": ["request_received", "model_invoked", "response_ready"],
        }


class ReleaseSlimGenerationService:
    def generate(
        self,
        task_text: str,
        provided_context: str | None = None,
        *,
        archetype: str | None = None,
        output_mode: str | None = None,
        input_roots: list[str] | None = None,
        risk_tags: list[str] | None = None,
        debug: bool = False,
        mode: str = "release",
        model: str | None = None,
        runtime_options: dict[str, int | float] | None = None,
        allow_cloud_model: bool = False,
        language: str = "ru",
        repair_budget: int = 2,
    ) -> dict[str, object]:
        assert task_text == "make a LocalScript"
        assert provided_context == "inventory payload"
        assert debug is False
        assert mode == "releaseSlim"
        assert model is None
        assert runtime_options == {
            "num_ctx": 4096,
            "num_predict": 256,
            "batch": 1,
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 1.5,
            "repeat_penalty": 1.0,
        }
        assert allow_cloud_model is False
        assert language == "ru"
        assert repair_budget == 2
        return {
            "code": "print('release slim')",
            "validation_status": "not_run",
            "stop_reason": "not_run",
            "trace": ["request_received", "response_ready"],
        }


class LanguageGenerationService:
    def generate(
        self,
        task_text: str,
        provided_context: str | None = None,
        *,
        archetype: str | None = None,
        output_mode: str | None = None,
        input_roots: list[str] | None = None,
        risk_tags: list[str] | None = None,
        debug: bool = False,
        mode: str = "release",
        model: str | None = None,
        runtime_options: dict[str, int] | None = None,
        allow_cloud_model: bool = False,
        language: str = "ru",
        repair_budget: int = 2,
    ) -> dict[str, object]:
        assert language == "en"
        assert repair_budget == 2
        return {
            "code": "print('ok')",
            "validation_status": "not_run",
            "stop_reason": "not_run",
            "trace": ["request_received", "response_ready"],
        }


class PlanGenerationService:
    def generate(self, **_: object) -> dict[str, object]:
        raise AssertionError("plan preflight must not call generator")

    def plan(
        self,
        task_text: str,
        provided_context: str | None = None,
        *,
        archetype: str | None = None,
        output_mode: str | None = None,
        input_roots: list[str] | None = None,
        risk_tags: list[str] | None = None,
        debug: bool = False,
        mode: str = "release",
        model: str | None = None,
        runtime_options: dict[str, int | float] | None = None,
        allow_cloud_model: bool = False,
        language: str = "ru",
    ) -> dict[str, object]:
        assert task_text == "make a LocalScript"
        assert provided_context == "inventory payload"
        assert archetype is None
        assert output_mode is None
        assert input_roots is None
        assert risk_tags is None
        assert debug is False
        assert mode == "release"
        assert model is None
        assert runtime_options == {"num_ctx": 4096, "num_predict": 256, "batch": 1, "temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5, "repeat_penalty": 1.0, "num_gpu": -1}
        assert allow_cloud_model is False
        assert language == "ru"
        return {
            "task_spec": {
                "task_text": task_text,
                "language": language,
                "archetype": "datetime_conversion",
                "operation": "datetime_formatting",
                "output_mode": "clarification",
                "input_roots": ["wf.vars.date"],
                "expected_shape": "clarification_question",
                "risk_tags": ["invalid_date"],
                "edge_cases": ["invalid_format"],
                "clarification_required": True,
                "clarification_question": "Что вернуть, если дата некорректна?",
                "clarification_questions": [
                    {
                        "id": "invalid_date_behavior",
                        "question": "Что вернуть, если дата некорректна?",
                        "options": [
                            {"id": "empty_string", "label": "пустую строку", "description": ""},
                            {"id": "nil", "label": "nil", "description": ""},
                        ],
                        "default_option_id": "empty_string",
                    }
                ],
            },
            "clarification_required": True,
            "questions": [
                {
                    "id": "invalid_date_behavior",
                    "question": "Что вернуть, если дата некорректна?",
                    "options": [
                        {"id": "empty_string", "label": "пустую строку", "description": ""},
                        {"id": "nil", "label": "nil", "description": ""},
                    ],
                    "default_option_id": "empty_string",
                }
            ],
            "trace": ["request_received", "planner", "response_ready"],
            "debug": None,
        }


class FeedbackGenerationService:
    def generate(
        self,
        task_text: str,
        provided_context: str | None = None,
        *,
        feedback_text: str | None = None,
        previous_candidate: str | None = None,
        assisted_repair_option_id: str | None = None,
        **_: object,
    ) -> dict[str, object]:
        assert task_text == "make a LocalScript"
        assert provided_context == "inventory payload"
        assert feedback_text == "Store the result in wf.vars.lastEmail."
        assert previous_candidate == "return wf.vars.emails[#wf.vars.emails]"
        assert assisted_repair_option_id is None
        return {
            "code": "return wf.vars.lastEmail",
            "validation_status": "passed",
            "stop_reason": "passed",
            "trace": ["request_received", "feedback_received", "planner", "response_ready"],
        }


class StreamingProgressGenerationService:
    def generate(
        self,
        task_text: str,
        provided_context: str | None = None,
        *,
        archetype: str | None = None,
        output_mode: str | None = None,
        input_roots: list[str] | None = None,
        risk_tags: list[str] | None = None,
        debug: bool = False,
        mode: str = "release",
        model: str | None = None,
        runtime_options: dict[str, int] | None = None,
        allow_cloud_model: bool = False,
        language: str = "ru",
        repair_budget: int = 2,
        progress_callback=None,
    ) -> dict[str, object]:
        assert task_text == "make a LocalScript"
        assert callable(progress_callback)
        for stage in (
            "request_received",
            "planner",
            "prompter",
            "generation",
            "deterministic_validation",
            "response_ready",
        ):
            progress_callback(stage)
        return {
            "code": "print('ok')",
            "validation_status": "passed",
            "stop_reason": "passed",
            "trace": [
                "request_received",
                "planner",
                "prompter",
                "generation",
                "deterministic_validation",
                "response_ready",
            ],
        }


def test_generate_returns_code_validation_status_and_trace() -> None:
    client = TestClient(app)
    app.dependency_overrides[get_generation_service] = lambda: FakeGenerationService()

    try:
        response = client.post(
            "/generate",
            json={
                "task_text": "make a LocalScript",
                "provided_context": "inventory payload",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "code": "print('ok')",
        "validation_status": "not_run",
        "stop_reason": "not_run",
        "trace": ["request_received", "response_ready"],
        "validator_report": None,
        "critic_report": None,
        "repair_count": 0,
        "clarification_count": 0,
        "assisted_repair_request": None,
        "output_mode": None,
        "archetype": None,
        "debug": None,
    }


def test_generate_passes_explicit_language_to_service() -> None:
    client = TestClient(app)
    app.dependency_overrides[get_generation_service] = lambda: LanguageGenerationService()

    try:
        response = client.post(
            "/generate",
            json={
                "task_text": "make a LocalScript",
                "provided_context": "inventory payload",
                "language": "en",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["code"] == "print('ok')"


def test_plan_returns_task_spec_and_questions_without_generation() -> None:
    client = TestClient(app)
    app.dependency_overrides[get_generation_service] = lambda: PlanGenerationService()

    try:
        response = client.post(
            "/plan",
            json={
                "task_text": "make a LocalScript",
                "provided_context": "inventory payload",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "task_spec": {
            "task_text": "make a LocalScript",
            "language": "ru",
            "archetype": "datetime_conversion",
            "operation": "datetime_formatting",
            "output_mode": "clarification",
            "input_roots": ["wf.vars.date"],
            "expected_shape": "clarification_question",
            "risk_tags": ["invalid_date"],
            "edge_cases": ["invalid_format"],
            "clarification_required": True,
            "clarification_question": "Что вернуть, если дата некорректна?",
            "clarification_questions": [
                {
                    "id": "invalid_date_behavior",
                    "question": "Что вернуть, если дата некорректна?",
                    "options": [
                        {"id": "empty_string", "label": "пустую строку", "description": ""},
                        {"id": "nil", "label": "nil", "description": ""},
                    ],
                    "default_option_id": "empty_string",
                }
            ],
        },
        "clarification_required": True,
        "questions": [
            {
                "id": "invalid_date_behavior",
                "question": "Что вернуть, если дата некорректна?",
                "options": [
                    {"id": "empty_string", "label": "пустую строку", "description": ""},
                    {"id": "nil", "label": "nil", "description": ""},
                ],
                "default_option_id": "empty_string",
            }
        ],
        "trace": ["request_received", "planner", "response_ready"],
        "debug": None,
    }


def test_generate_passes_feedback_metadata_to_service() -> None:
    client = TestClient(app)
    app.dependency_overrides[get_generation_service] = lambda: FeedbackGenerationService()

    try:
        response = client.post(
            "/generate",
            json={
                "task_text": "make a LocalScript",
                "provided_context": "inventory payload",
                "feedback_text": "Store the result in wf.vars.lastEmail.",
                "previous_candidate": "return wf.vars.emails[#wf.vars.emails]",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["trace"] == ["request_received", "feedback_received", "planner", "response_ready"]


def test_generate_progress_streams_stage_events_before_final_payload() -> None:
    client = TestClient(app)
    app.dependency_overrides[get_generation_service] = lambda: StreamingProgressGenerationService()

    try:
        with client.stream("POST", "/generate/progress", json={"task_text": "make a LocalScript"}) as response:
            events = [json.loads(line) for line in response.iter_lines() if line]
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert [event["stage"] for event in events if event["type"] == "progress"] == [
        "request_received",
        "planner",
        "prompter",
        "generation",
        "deterministic_validation",
        "response_ready",
    ]
    assert events[-1] == {
        "type": "final",
        "payload": {
            "code": "print('ok')",
            "validation_status": "passed",
            "stop_reason": "passed",
            "trace": [
                "request_received",
                "planner",
                "prompter",
                "generation",
                "deterministic_validation",
                "response_ready",
            ],
            "validator_report": None,
            "critic_report": None,
            "repair_count": 0,
            "clarification_count": 0,
            "assisted_repair_request": None,
            "output_mode": None,
            "archetype": None,
            "debug": None,
        },
    }


def test_generate_passes_quality_metadata_and_returns_quality_state() -> None:
    client = TestClient(app)
    app.dependency_overrides[get_generation_service] = lambda: QualityGenerationService()

    try:
        response = client.post(
            "/generate",
            json={
                "task_text": "make a LocalScript",
                "provided_context": "inventory payload",
                "archetype": "simple_extraction",
                "output_mode": "raw_lua",
                "input_roots": ["wf.vars.emails"],
                "risk_tags": ["array_indexing", "empty_array"],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "code": "return wf.vars.emails[#wf.vars.emails]",
        "validation_status": "passed",
        "stop_reason": "passed",
        "trace": [
            "request_received",
            "generation",
            "format_validation",
            "rule_validation",
            "finalize",
        ],
        "validator_report": {
            "status": "pass",
            "iterations": [],
        },
        "critic_report": None,
        "repair_count": 0,
        "clarification_count": 0,
        "assisted_repair_request": None,
        "output_mode": "raw_lua",
        "archetype": "simple_extraction",
        "debug": None,
    }


def test_generate_returns_debug_payload_when_requested() -> None:
    client = TestClient(app)
    app.dependency_overrides[get_generation_service] = lambda: DebugGenerationService()

    try:
        response = client.post(
            "/generate",
            json={
                "task_text": "make a LocalScript",
                "provided_context": "inventory payload",
                "debug": True,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["debug"] == {
        "prompt_package": {"prompt": "PROMPT"},
        "model_calls": [{"phase": "generation", "prompt": "PROMPT", "raw_response": "```lua\nreturn x\n```"}],
        "validation_passes": [],
    }


def test_generate_passes_runtime_policy_fields() -> None:
    client = TestClient(app)
    app.dependency_overrides[get_generation_service] = lambda: RuntimePolicyGenerationService()

    try:
        response = client.post(
            "/generate",
            json={
                "task_text": "make a LocalScript",
                "provided_context": "inventory payload",
                "debug": True,
                "mode": "debug",
                "model": "qwen3.5:9b",
                "runtime_options": {"num_ctx": 2048, "num_predict": 128, "batch": 1, "num_gpu": -1},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["code"] == "print('debug')"


def test_generate_release_slim_injects_default_runtime_options_without_gpu_pin() -> None:
    client = TestClient(app)
    app.dependency_overrides[get_generation_service] = lambda: ReleaseSlimGenerationService()

    try:
        response = client.post(
            "/generate",
            json={
                "task_text": "make a LocalScript",
                "provided_context": "inventory payload",
                "mode": "releaseSlim",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["code"] == "print('release slim')"


def test_generate_rejects_cloud_model_in_release_before_model_call() -> None:
    client = TestClient(app)

    response = client.post(
        "/generate",
        json={
            "task_text": "make a LocalScript",
            "model": "gpt-oss:20b-cloud",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "cloud_model_not_allowed"


def test_generate_returns_normalized_validation_errors() -> None:
    client = TestClient(app)

    response = client.post("/generate", json={})

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "Invalid request payload.",
            "details": [
                {
                    "field": "body.task_text",
                    "message": "Field required",
                    "type": "missing",
                }
            ],
        }
    }


def test_generate_returns_normalized_runtime_errors() -> None:
    client = TestClient(app)
    app.dependency_overrides[get_generation_service] = lambda: FailingGenerationService()

    try:
        response = client.post("/generate", json={"task_text": "make a LocalScript"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "model_error",
            "message": "Local model request failed.",
            "details": [],
        }
    }


def test_generate_emits_structured_logs(caplog) -> None:
    client = TestClient(app)
    app.dependency_overrides[get_generation_service] = lambda: FakeGenerationService()

    with caplog.at_level(logging.INFO, logger="luamts.api"):
        try:
            response = client.post(
                "/generate",
                json={
                    "task_text": "make a LocalScript",
                    "provided_context": "inventory payload",
                },
            )
        finally:
            app.dependency_overrides.clear()

    assert response.status_code == 200

    messages = [json.loads(record.message) for record in caplog.records if record.name == "luamts.api"]
    assert messages == [
        {"debug": False, "event": "generate_requested", "path": "/generate"},
        {
            "debug": False,
            "event": "generate_completed",
            "path": "/generate",
            "validation_status": "not_run",
        },
    ]


