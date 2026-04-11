import json
from pathlib import Path

import httpx

from adapters import model as model_module
from adapters.model import OllamaModelAdapter


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
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"response": self._response_text}


class RecordingHttpClient:
    def __init__(self, response_text: str) -> None:
        self.calls: list[dict[str, object]] = []
        self._response_text = response_text

    def post(self, url: str, json: dict[str, object], timeout: float) -> ConfigurableResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return ConfigurableResponse(self._response_text)


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
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b")

    http_client = FakeHttpClient()
    adapter = OllamaModelAdapter(http_client=http_client)

    code = adapter.generate("make a LocalScript", "inventory payload")

    assert code == "print('ok')"
    assert http_client.calls == [
        {
            "url": "http://localhost:11434/api/generate",
            "json": {
                "model": "qwen2.5-coder:3b",
                "prompt": "make a LocalScript\n\ninventory payload",
                "stream": False,
            },
            "timeout": 60.0,
        }
    ]


def test_ollama_adapter_falls_back_to_local_cli_when_http_api_is_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b")

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
        ["ollama", "run", "qwen2.5-coder:3b", "Return a one-line Lua print statement."]
    ]


def test_ollama_adapter_enforces_raw_lua_mode_prompt_and_normalizes_response(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b")

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
    assert len(http_client.calls) == 1
    prompt = str(http_client.calls[0]["json"]["prompt"])
    assert "Output mode: raw_lua" in prompt
    assert "Return only Lua code." in prompt
    assert "Allowed data roots: wf.vars.emails" in prompt


def test_ollama_adapter_switches_to_clarification_when_data_roots_are_ambiguous(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b")

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
    prompt = str(http_client.calls[0]["json"]["prompt"])
    assert "Output mode: clarification" in prompt
    assert "Ask one focused clarification question instead of generating code." in prompt


def test_ollama_adapter_normalizes_json_wrapper_mode(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b")

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
    prompt = str(http_client.calls[0]["json"]["prompt"])
    assert "Output mode: json_wrapper" in prompt
    assert "Every string value that contains generated code must use the lua{...}lua wrapper." in prompt


def test_ollama_adapter_normalizes_patch_mode(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b")

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
    prompt = str(http_client.calls[0]["json"]["prompt"])
    assert "Output mode: patch_mode" in prompt
    assert "Return only the fields that need to be added or changed." in prompt


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
