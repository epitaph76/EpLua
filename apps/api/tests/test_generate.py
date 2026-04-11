import json
import logging

from fastapi.testclient import TestClient

from errors import ApiError
from main import app
from routes.generate import get_generation_service


class FakeGenerationService:
    def generate(self, task_text: str, provided_context: str | None = None) -> dict[str, object]:
        assert task_text == "make a LocalScript"
        assert provided_context == "inventory payload"

        return {
            "code": "print('ok')",
            "validation_status": "not_run",
            "trace": ["request_received", "response_ready"],
        }


class FailingGenerationService:
    def generate(self, task_text: str, provided_context: str | None = None) -> dict[str, object]:
        raise ApiError(status_code=502, code="model_error", message="Local model request failed.")


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
        "trace": ["request_received", "response_ready"],
    }


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
        {"event": "generate_requested", "path": "/generate"},
        {
            "event": "generate_completed",
            "path": "/generate",
            "validation_status": "not_run",
        },
    ]
