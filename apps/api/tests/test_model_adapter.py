import json
import re
from pathlib import Path

import httpx
import pytest

from adapters import model as model_module
from adapters.model import OllamaModelAdapter
from errors import ApiError
from runtime_policy import RuntimeOptions


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"response": "print('ok')"}


class FakeHttpClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, json: dict[str, object], timeout: float) -> FakeResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse()


class ConfigurableResponse:
    def __init__(self, response_text: str, *, chat: bool = False) -> None:
        self._response_text = response_text
        self._chat = chat

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        if self._chat:
            return {"message": {"role": "assistant", "content": self._response_text}}
        return {"response": self._response_text}


class RecordingHttpClient:
    def __init__(self, response_text: str) -> None:
        self.calls: list[dict[str, object]] = []
        self._response_text = response_text

    def post(self, url: str, json: dict[str, object], timeout: float) -> ConfigurableResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if url.endswith("/api/chat"):
            return ConfigurableResponse(
                _agent_response_for_payload(json, self._response_text),
                chat=True,
            )
        return ConfigurableResponse(self._response_text)


class PayloadResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class SequencedPayloadHttpClient:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.calls: list[dict[str, object]] = []
        self._payloads = payloads

    def post(self, url: str, json: dict[str, object], timeout: float) -> PayloadResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return PayloadResponse(self._payloads.pop(0))


class EmptyAgentChatFallbackHttpClient:
    def __init__(self, generate_response_text: str) -> None:
        self.calls: list[dict[str, object]] = []
        self._generate_response_text = generate_response_text

    def post(self, url: str, json: dict[str, object], timeout: float) -> PayloadResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if url.endswith("/api/chat"):
            return PayloadResponse({"message": {"role": "assistant", "content": ""}})
        return PayloadResponse({"response": self._generate_response_text})


def _recorded_agent_prompt(http_client: RecordingHttpClient, *, index: int = -1) -> tuple[str, list[dict[str, str]]]:
    assert http_client.calls[index]["url"] == "http://localhost:11434/api/chat"
    payload = http_client.calls[index]["json"]
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert [message["role"] for message in messages] == ["system", "user"]
    return "\n\n".join(str(message["content"]) for message in messages), messages


def _agent_response_for_payload(payload: dict[str, object], generator_response: str) -> str:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return generator_response
    first_message = messages[0]
    if not isinstance(first_message, dict):
        return generator_response
    system_prompt = str(first_message.get("content", ""))
    if "planner agent" in system_prompt:
        return _default_planner_response(messages)
    if "prompter agent" in system_prompt:
        return "not-json"
    return generator_response


def _default_planner_response(messages: list[object]) -> str:
    user_message = str(messages[1].get("content", "")) if len(messages) > 1 and isinstance(messages[1], dict) else ""
    lowered = user_message.lower()
    roots = tuple(dict.fromkeys(re.findall(r"wf\.(?:vars|initVariables)\.[A-Za-z0-9_\.]+", user_message)))
    output_mode = _planned_output_mode(lowered)
    clarification_required = (
        '"explicit_input_basis":false' in lowered
        and any(root.startswith("wf.vars.") for root in roots)
        and any(root.startswith("wf.initVariables.") for root in roots)
    )
    operation = _planned_operation(lowered, output_mode)
    return json.dumps(
        {
            "operation": operation,
            "output_mode": "clarification" if clarification_required else output_mode,
            "input_roots": list(roots),
            "expected_shape": "clarification_question"
            if clarification_required
            else _planned_expected_shape(operation, output_mode),
            "risk_tags": [],
            "edge_cases": ["single_item", "empty_array"] if operation in {"last_array_item", "first_array_item"} else [],
            "clarification_required": clarification_required,
            "clarification_question": (
                "Какой источник данных использовать: wf.vars.emails или wf.initVariables.recallTime?"
                if clarification_required
                else None
            ),
            "task_intents": _planned_task_intents(lowered),
        }
    )


def _planned_output_mode(lowered_prompt: str) -> str:
    if '"output_mode":"patch_mode"' in lowered_prompt:
        return "patch_mode"
    if '"output_mode":"json_wrapper"' in lowered_prompt:
        return "json_wrapper"
    if '"output_mode":"clarification"' in lowered_prompt:
        return "clarification"
    return "raw_lua"


def _planned_operation(lowered_prompt: str, output_mode: str) -> str:
    if "послед" in lowered_prompt or "last" in lowered_prompt:
        return "last_array_item"
    if "перв" in lowered_prompt or "first" in lowered_prompt:
        return "first_array_item"
    if "datetime_conversion" in lowered_prompt:
        return "datetime_conversion"
    if output_mode == "patch_mode":
        return "additive_patch"
    if "filtering" in lowered_prompt:
        return "array_filter"
    return "direct_extraction"


def _planned_expected_shape(operation: str, output_mode: str) -> str:
    if output_mode == "patch_mode":
        return "json_object_patch"
    if output_mode == "json_wrapper":
        return "json_object_with_wrapped_code"
    if operation in {"last_array_item", "first_array_item", "direct_extraction"}:
        return "scalar_or_nil"
    if operation == "array_filter":
        return "array"
    return "lua_value"


def _planned_task_intents(lowered_prompt: str) -> list[str]:
    intents: list[str] = []
    if "очист" in lowered_prompt or "clear " in lowered_prompt:
        intents.append("clear_target_fields")
    if "удали" in lowered_prompt or "remove" in lowered_prompt:
        intents.append("remove_target_fields")
    if "оставь только" in lowered_prompt or "keep only" in lowered_prompt:
        intents.append("keep_only_target_fields")
    if "не трогай" in lowered_prompt or "остальные поля" in lowered_prompt or "preserve untouched" in lowered_prompt:
        intents.append("preserve_untouched_fields")
    if "в существующ" in lowered_prompt or "in place" in lowered_prompt:
        intents.append("mutate_in_place")
    return list(dict.fromkeys(intents))


class UnavailableResponse:
    def __init__(self) -> None:
        self.status_code = 503
        self.request = httpx.Request("POST", "http://localhost:11434/api/generate")

    def raise_for_status(self) -> None:
        raise httpx.HTTPStatusError("service unavailable", request=self.request, response=self)


class UnavailableHttpClient:
    def post(self, url: str, json: dict[str, object], timeout: float) -> UnavailableResponse:
        return UnavailableResponse()


def test_ollama_adapter_calls_local_generate_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")
    monkeypatch.delenv("OLLAMA_REQUEST_TIMEOUT", raising=False)

    http_client = FakeHttpClient()
    adapter = OllamaModelAdapter(http_client=http_client)

    code = adapter.generate("make a LocalScript", "inventory payload")

    assert code == "print('ok')"
    assert http_client.calls == [
        {
            "url": "http://localhost:11434/api/generate",
            "json": {
                "model": "qwen3.5:9b",
                "prompt": "make a LocalScript\n\ninventory payload",
                "stream": False,
                "think": False,
                "options": {
                    "num_ctx": 4096,
                    "num_predict": 256,
                    "batch": 1,
                    "temperature": 0.7,
                    "top_p": 0.8,
                    "top_k": 20,
                    "min_p": 0.0,
                    "presence_penalty": 1.5,
                    "repeat_penalty": 1.0,
                },
            },
            "timeout": 180.0,
        }
    ]


def test_ollama_adapter_defaults_to_loopback_ip_base_url(monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")
    monkeypatch.delenv("OLLAMA_REQUEST_TIMEOUT", raising=False)

    http_client = FakeHttpClient()
    adapter = OllamaModelAdapter(http_client=http_client)

    code = adapter.generate("make a LocalScript", "inventory payload")

    assert code == "print('ok')"
    assert http_client.calls[0]["url"] == "http://127.0.0.1:11434/api/generate"
    assert http_client.calls[0]["timeout"] == 180.0


def test_ollama_adapter_allows_docker_service_hostname(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")

    http_client = FakeHttpClient()
    adapter = OllamaModelAdapter(http_client=http_client)

    code = adapter.generate("make a LocalScript", "inventory payload")

    assert code == "print('ok')"
    assert http_client.calls[0]["url"] == "http://ollama:11434/api/generate"


def test_ollama_adapter_uses_env_override_for_request_timeout(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")
    monkeypatch.setenv("OLLAMA_REQUEST_TIMEOUT", "240")

    http_client = FakeHttpClient()
    adapter = OllamaModelAdapter(http_client=http_client)

    adapter.generate("make a LocalScript", "inventory payload")

    assert http_client.calls[0]["timeout"] == 240.0


def test_ollama_adapter_rejects_cloud_model_in_release(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")

    with pytest.raises(ApiError, match="Cloud Ollama model tags are not allowed"):
        OllamaModelAdapter(http_client=FakeHttpClient(), model="gpt-oss:20b-cloud")


def test_ollama_adapter_rejects_cloud_model_in_debug_without_explicit_flag(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")

    with pytest.raises(ApiError, match="--allow-cloud-model"):
        OllamaModelAdapter(
            http_client=FakeHttpClient(),
            model="gpt-oss:20b-cloud",
            mode="debug",
        )


def test_ollama_adapter_allows_cloud_model_in_debug_with_explicit_flag(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")

    adapter = OllamaModelAdapter(
        http_client=FakeHttpClient(),
        model="gpt-oss:20b-cloud",
        mode="debug",
        allow_cloud_model=True,
    )

    assert adapter.generate_from_prompt("return ok") == "print('ok')"


def test_ollama_adapter_falls_back_to_local_cli_when_http_api_is_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")

    recorded_commands: list[list[str]] = []

    class CompletedProcess:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def fake_run(command: list[str], capture_output: bool, text: bool, check: bool) -> CompletedProcess:
        recorded_commands.append(command)
        assert capture_output is True
        assert text is True
        assert check is True
        return CompletedProcess('print("Hello, World!")\n')

    monkeypatch.setattr(model_module.subprocess, "run", fake_run)

    adapter = OllamaModelAdapter(http_client=UnavailableHttpClient())

    code = adapter.generate("Return a one-line Lua print statement.")

    assert code == 'print("Hello, World!")'
    assert recorded_commands == [
        ["ollama", "run", "--think=false", "qwen3.5:9b", "Return a one-line Lua print statement."]
    ]


def test_ollama_adapter_enforces_raw_lua_mode_prompt_and_normalizes_response(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")

    http_client = RecordingHttpClient(
        "Here is the Lua you asked for:\n```lua\nreturn wf.vars.emails[#wf.vars.emails]\n```\n"
    )
    adapter = OllamaModelAdapter(http_client=http_client)

    code = adapter.generate(
        "Из полученного списка email получи последний.",
        '{"wf":{"vars":{"emails":["user1@example.com","user2@example.com"]}}}',
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
    )

    assert code == "return wf.vars.emails[#wf.vars.emails]"
    assert [call["url"] for call in http_client.calls] == [
        "http://localhost:11434/api/chat",
        "http://localhost:11434/api/chat",
        "http://localhost:11434/api/chat",
    ]
    assert all(call["json"]["think"] is False for call in http_client.calls)
    prompt, _messages = _recorded_agent_prompt(http_client)
    assert "Task:" in prompt
    assert "Из полученного списка email получи последний." in prompt
    assert "Provided context:" in prompt
    assert '{"wf":{"vars":{"emails":["user1@example.com","user2@example.com"]}}}' in prompt
    assert "Output mode: raw_lua" in prompt
    assert "Return only Lua code." in prompt
    assert "Allowed data roots: wf.vars.emails" in prompt


def test_ollama_adapter_retries_planner_when_agent_output_hits_num_predict_budget(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")

    http_client = SequencedPayloadHttpClient(
        [
            {
                "message": {
                    "role": "assistant",
                    "content": (
                        '{"arch":"simple_extraction","op":"last_array_item","mode":"raw_lua",'
                        '"roots":["wf.vars.emails"],"shape":"scalar_or_nil","risks":["array_indexing"],"edges":["single_item","empty_array"],"clar'
                    ),
                },
                "eval_count": 256,
            },
            {
                "message": {
                    "role": "assistant",
                    "content": (
                        '{"arch":"simple_extraction","op":"last_array_item","mode":"raw_lua",'
                        '"roots":["wf.vars.emails"],"shape":"scalar_or_nil","risks":["array_indexing","empty_array"],'
                        '"edges":["single_item","empty_array"],"clar":false,"q":null,"intents":[]}'
                    ),
                },
                "eval_count": 96,
            },
            {
                "message": {
                    "role": "assistant",
                    "content": '{"sys":["Return the last item only."],"user":["Use wf.vars.emails[#wf.vars.emails]."]}',
                },
                "eval_count": 48,
            },
            {
                "message": {
                    "role": "assistant",
                    "content": "return wf.vars.emails[#wf.vars.emails]",
                },
                "eval_count": 24,
            },
        ]
    )
    adapter = OllamaModelAdapter(
        http_client=http_client,
        runtime_options=RuntimeOptions(num_ctx=4096, num_predict=256, batch=1),
    )

    code = adapter.generate(
        "Из полученного списка email получи последний.",
        '{"wf":{"vars":{"emails":["user1@example.com","user2@example.com"]}}}',
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
    )

    assert code == "return wf.vars.emails[#wf.vars.emails]"
    assert [call["url"] for call in http_client.calls] == [
        "http://localhost:11434/api/chat",
        "http://localhost:11434/api/chat",
        "http://localhost:11434/api/chat",
        "http://localhost:11434/api/chat",
    ]
    retry_messages = http_client.calls[1]["json"]["messages"]
    assert isinstance(retry_messages, list)
    assert any("truncated by the token budget" in str(message["content"]) for message in retry_messages)


def test_ollama_adapter_retries_agentic_generate_prompt_when_output_hits_num_predict_budget(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")

    http_client = SequencedPayloadHttpClient(
        [
            {
                "response": (
                    '{"arch":"simple_extraction","op":"last_array_item","mode":"raw_lua",'
                    '"roots":["wf.vars.emails"],"shape":"scalar_or_nil","risks":["array_indexing"],"edges":["single_item","empty_array"],"clar'
                ),
                "eval_count": 256,
            },
            {
                "response": (
                    '{"arch":"simple_extraction","op":"last_array_item","mode":"raw_lua",'
                    '"roots":["wf.vars.emails"],"shape":"scalar_or_nil","risks":["array_indexing","empty_array"],'
                    '"edges":["single_item","empty_array"],"clar":false,"q":null,"intents":[]}'
                ),
                "eval_count": 96,
            },
        ]
    )
    adapter = OllamaModelAdapter(
        http_client=http_client,
        runtime_options=RuntimeOptions(num_ctx=4096, num_predict=256, batch=1),
    )

    response = adapter.generate_from_prompt(
        "SYSTEM:\nYou are the planner agent for the luaMTS validation pipeline.\n\nUSER:\nTask:"
    )

    assert response.endswith('"intents":[]}')
    assert len(http_client.calls) == 2
    assert "truncated by the token budget" in str(http_client.calls[1]["json"]["prompt"])


def test_ollama_adapter_exposes_prompt_generation_metadata_without_retrying(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")

    http_client = SequencedPayloadHttpClient(
        [
            {
                "response": '{"result":"lua{return wf.vars.em',
                "eval_count": 256,
            }
        ]
    )
    adapter = OllamaModelAdapter(
        http_client=http_client,
        runtime_options=RuntimeOptions(num_ctx=4096, num_predict=256, batch=1),
    )

    result = adapter.generate_from_prompt_with_metadata("GENERATOR PROMPT")

    assert result == {
        "response": '{"result":"lua{return wf.vars.em',
        "eval_count": 256,
        "num_predict": 256,
    }
    assert len(http_client.calls) == 1
    assert http_client.calls[0]["json"]["prompt"] == "GENERATOR PROMPT"


def test_ollama_adapter_falls_back_to_legacy_prompt_when_agent_chat_content_is_empty(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")

    http_client = EmptyAgentChatFallbackHttpClient(
        '{"op":"last_array_item","mode":"raw_lua","roots":["wf.vars.emails"],"shape":"scalar_or_nil","risks":["array_indexing","empty_array"],"edges":["single_item","empty_array"],"clar":false,"q":null,"intents":[]}'
    )
    adapter = OllamaModelAdapter(http_client=http_client)
    agent_prompt = model_module.AgentPrompt(
        agent_name="planner",
        messages=(
            model_module.AgentMessage(role="system", content="You are the planner agent for the luaMTS validation pipeline."),
            model_module.AgentMessage(role="user", content="Task:\nИз полученного списка email получи последний."),
        ),
    )

    response = adapter.generate_from_agent(agent_prompt)

    assert response.startswith('{"op":"last_array_item"')
    assert [call["url"] for call in http_client.calls] == [
        "http://localhost:11434/api/chat",
        "http://localhost:11434/api/generate",
    ]
    assert "SYSTEM:" in str(http_client.calls[1]["json"]["prompt"])
    assert "planner agent for the luaMTS validation pipeline" in str(http_client.calls[1]["json"]["prompt"])


def test_ollama_adapter_switches_to_clarification_when_data_roots_are_ambiguous(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")

    http_client = RecordingHttpClient(
        "Какой источник данных использовать: wf.vars.emails или wf.initVariables.recallTime?"
    )
    adapter = OllamaModelAdapter(http_client=http_client)

    code = adapter.generate(
        "Сконвертируй время и пришли результат.",
        '{"wf":{"vars":{"emails":["user@example.com"]},"initVariables":{"recallTime":"2023-10-15T15:30:00+00:00"}}}',
        archetype="datetime_conversion",
        output_mode="raw_lua",
        risk_tags=["array_indexing", "init_variables"],
    )

    assert code == "Какой источник данных использовать: wf.vars.emails или wf.initVariables.recallTime?"
    prompt, _messages = _recorded_agent_prompt(http_client)
    assert "Output mode: clarification" in prompt
    assert "Ask one focused clarification question instead of generating code." in prompt


def test_ollama_adapter_normalizes_json_wrapper_mode(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")

    http_client = RecordingHttpClient(
        'Ответ:\n{"lastEmail":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}\n'
    )
    adapter = OllamaModelAdapter(http_client=http_client)

    code = adapter.generate(
        "Из полученного списка email получи последний.",
        '{"wf":{"vars":{"emails":["user1@example.com","user2@example.com"]}}}',
        archetype="simple_extraction",
        output_mode="json_wrapper",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
    )

    assert code == '{"lastEmail":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}'
    prompt, _messages = _recorded_agent_prompt(http_client)
    assert "Output mode: json_wrapper" in prompt
    assert "Every string value that contains generated code must use the lua{...}lua wrapper." in prompt


def test_ollama_adapter_normalizes_patch_mode(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")

    http_client = RecordingHttpClient(
        'Патч:\n{"num":"lua{return tonumber(\'5\')}lua","squared":"lua{local n = tonumber(\'5\')\\nreturn n * n}lua"}'
    )
    adapter = OllamaModelAdapter(http_client=http_client)

    code = adapter.generate(
        "Добавь переменную с квадратом числа.",
        None,
        archetype="transformation",
        output_mode="patch_mode",
        input_roots=[],
        risk_tags=["patch_payload", "numeric_transform", "no_full_rewrite"],
    )

    assert (
        code
        == '{"num":"lua{return tonumber(\'5\')}lua","squared":"lua{local n = tonumber(\'5\')\\nreturn n * n}lua"}'
    )
    prompt, _messages = _recorded_agent_prompt(http_client)
    assert "Output mode: patch_mode" in prompt
    assert "Return only the fields that need to be added or changed." in prompt


def test_ollama_adapter_excludes_retrieval_context_from_domain_prompt(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")

    http_client = RecordingHttpClient("return wf.vars.emails[#wf.vars.emails]")
    adapter = OllamaModelAdapter(http_client=http_client)

    adapter.generate(
        "Из полученного списка email получи последний.",
        '{"wf":{"vars":{"emails":["user1@example.com","user2@example.com"]}}}',
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
    )

    prompt, _messages = _recorded_agent_prompt(http_client)
    assert "Retrieved guidance:" not in prompt
    assert "Similar examples:" not in prompt
    assert "case-01-last-array-item" not in prompt
    assert "raw_lua=return wf.vars.emails[#wf.vars.emails]" not in prompt
    assert "raw_lua=return wf.vars.emails\n" not in prompt


def test_ollama_adapter_includes_resolved_task_intents_in_domain_prompt(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")

    http_client = RecordingHttpClient("return wf.vars.RESTbody.result")
    adapter = OllamaModelAdapter(http_client=http_client)

    adapter.generate(
        "Очисти значения полей ID, ENTITY_ID и CALL, остальные поля не трогай.",
        '{"wf":{"vars":{"RESTbody":{"result":[{"ID":"1","ENTITY_ID":"2","CALL":"3","NAME":"Alice"}]}}}}',
        archetype="transformation",
        output_mode="raw_lua",
        input_roots=["wf.vars.RESTbody.result"],
        risk_tags=["table_mutation", "field_value_clearing", "nil_handling"],
    )

    prompt, _messages = _recorded_agent_prompt(http_client)
    assert "Resolved task intents:" in prompt
    assert "- clear_target_fields" in prompt
    assert "- preserve_untouched_fields" in prompt
    assert "When the task says to clear field values" in prompt


def test_public_benchmark_modes_match_expected_output_shapes() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    benchmark_path = repo_root / "benchmark" / "public_cases.json"
    registry_path = repo_root / "packages" / "task-archetypes" / "registry.json"

    with benchmark_path.open("r", encoding="utf-8") as benchmark_file:
        benchmark_cases = json.load(benchmark_file)["cases"]

    with registry_path.open("r", encoding="utf-8") as registry_file:
        archetype_registry = json.load(registry_file)

    for case in benchmark_cases:
        allowed_output_modes = case["allowed_output_modes"]
        primary_output_mode = case["primary_output_mode"]
        expected_outputs = case["expected_outputs"]
        registry_modes = archetype_registry[case["archetype"]]["allowed_output_modes"]

        assert primary_output_mode in allowed_output_modes
        assert sorted(expected_outputs.keys()) == sorted(allowed_output_modes)
        assert primary_output_mode in registry_modes

        for mode_name, expected_output in expected_outputs.items():
            if mode_name == "raw_lua":
                assert isinstance(expected_output, str)
                assert "```" not in expected_output
                continue

            assert isinstance(expected_output, dict)
            for wrapped_value in _string_leaves(expected_output):
                assert wrapped_value.startswith("lua{")
                assert wrapped_value.endswith("}lua")


def _string_leaves(node: object) -> list[str]:
    if isinstance(node, dict):
        leaves: list[str] = []
        for value in node.values():
            leaves.extend(_string_leaves(value))
        return leaves
    if isinstance(node, list):
        leaves: list[str] = []
        for value in node:
            leaves.extend(_string_leaves(value))
        return leaves
    if isinstance(node, str):
        return [node]
    return []

