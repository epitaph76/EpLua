import json
import subprocess
from pathlib import Path

from cli import main as cli_main


class FakeResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class RecordingHttpClient:
    def __init__(self) -> None:
        self.posts: list[dict[str, object]] = []
        self.gets: list[str] = []

    def __enter__(self) -> "RecordingHttpClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        return None

    def post(self, url: str, json: dict[str, object], timeout: float) -> FakeResponse:
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse(
            {
                "code": "return wf.vars.emails[#wf.vars.emails]",
                "validation_status": "passed",
                "trace": ["request_received", "finalize"],
            }
        )

    def get(self, url: str, timeout: float) -> FakeResponse:
        self.gets.append(url)
        return FakeResponse({"status": "ok", "models": []})


class SequencedHttpClient(RecordingHttpClient):
    def __init__(self, responses: list[dict[str, object]]) -> None:
        super().__init__()
        self._responses = responses

    def post(self, url: str, json: dict[str, object], timeout: float) -> FakeResponse:
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse(self._responses.pop(0))


class StreamingHttpClient(RecordingHttpClient):
    def __init__(self, lines: list[str]) -> None:
        super().__init__()
        self._lines = lines
        self.streams: list[dict[str, object]] = []

    def stream(self, method: str, url: str, json: dict[str, object], timeout: float):
        self.streams.append({"method": method, "url": url, "json": json, "timeout": timeout})
        return StreamingResponse(self._lines)


class StreamingResponse:
    status_code = 200

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def __enter__(self) -> "StreamingResponse":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        yield from self._lines


def test_cli_literal_print_preserves_lua_length_index_markup() -> None:
    class RecordingConsole:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def print(self, *objects: object, **kwargs: object) -> None:
            self.calls.append((objects, kwargs))

    console = RecordingConsole()

    cli_main._print_literal(console, "return wf.vars.emails[#wf.vars.emails]")

    assert console.calls == [
        (("return wf.vars.emails[#wf.vars.emails]",), {"markup": False})
    ]


def test_cli_literal_print_renders_escaped_newlines_as_lines() -> None:
    class RecordingConsole:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def print(self, *objects: object, **kwargs: object) -> None:
            self.calls.append((objects, kwargs))

    console = RecordingConsole()

    cli_main._print_literal(console, "lua{local emails = wf.vars.emails\\nreturn emails[#emails]}lua")

    assert console.calls == [
        (("lua{local emails = wf.vars.emails\nreturn emails[#emails]}lua",), {"markup": False})
    ]


def test_cli_generated_code_print_decodes_json_escaped_quotes_and_newlines() -> None:
    class RecordingConsole:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def print(self, *objects: object, **kwargs: object) -> None:
            self.calls.append((objects, kwargs))

    console = RecordingConsole()

    cli_main._print_generated_code(
        console,
        '{"result":"lua{if type(datum) ~= \\"string\\" then\\n  return \\"\\"\\nend}lua"}',
    )

    assert console.calls == [
        (
            (
                '{\n'
                '  "result": "lua{if type(datum) ~= "string" then\n'
                '  return ""\n'
                'end}lua"\n'
                '}',
            ),
            {"markup": False},
        )
    ]


def test_cli_debug_literal_preserves_json_escaped_newlines() -> None:
    class RecordingConsole:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def print(self, *objects: object, **kwargs: object) -> None:
            self.calls.append((objects, kwargs))

    console = RecordingConsole()

    cli_main._print_debug_literal(console, '{"raw_response":"lua{a\\nb}"}')

    assert console.calls == [
        (('{"raw_response":"lua{a\\nb}"}',), {"markup": False})
    ]


def test_cli_generate_release_calls_api_and_writes_report(tmp_path, monkeypatch, capsys) -> None:
    context_path = tmp_path / "context.json"
    context_path.write_text('{"wf":{"vars":{"emails":["a@example.com"]}}}', encoding="utf-8")
    report_path = tmp_path / "report.json"
    http_client = RecordingHttpClient()
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)

    exit_code = cli_main.main(
        [
            "generate",
            "--task",
            "Из массива emails верни последний email.",
            "--context",
            str(context_path),
            "--report",
            str(report_path),
        ]
    )

    assert exit_code == 0
    assert "return wf.vars.emails[#wf.vars.emails]" in capsys.readouterr().out
    assert http_client.posts == [
        {
            "url": "http://127.0.0.1:8011/generate",
            "json": {
                "task_text": "Из массива emails верни последний email.",
                "provided_context": '{"wf":{"vars":{"emails":["a@example.com"]}}}',
                "debug": False,
                "mode": "release",
                "runtime_options": {"num_ctx": 4096, "num_predict": 256, "batch": 1, "temperature": 0.8, "num_gpu": -1},
                "repair_budget": 2,
            },
            "timeout": 180.0,
        }
    ]
    assert json.loads(report_path.read_text(encoding="utf-8"))["response"]["validation_status"] == "passed"


def test_cli_generate_release_prints_live_api_progress_from_stream(monkeypatch, capsys) -> None:
    http_client = StreamingHttpClient(
        [
            '{"type":"progress","stage":"request_received","index":1}',
            '{"type":"progress","stage":"generation","index":2}',
            '{"type":"progress","stage":"deterministic_validation","index":3}',
            (
                '{"type":"final","payload":{'
                '"code":"return wf.vars.emails[#wf.vars.emails]",'
                '"validation_status":"passed",'
                '"stop_reason":"passed",'
                '"trace":["request_received","generation","deterministic_validation","response_ready"]'
                "}}"
            ),
        ]
    )
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)
    monkeypatch.setattr(cli_main.sys.stdout, "isatty", lambda: True)

    exit_code = cli_main.main(["generate", "--task", "Из массива emails верни последний email."])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Debug progress:" in output
    assert "слой 1: request_received прошёл" in output
    assert "слой 2: generation прошёл" in output
    assert "Status: passed" in output
    assert http_client.streams == [
        {
            "method": "POST",
            "url": "http://127.0.0.1:8011/generate/progress",
            "json": {
                "task_text": "Из массива emails верни последний email.",
                "provided_context": None,
                "debug": False,
                "mode": "release",
                "runtime_options": {"num_ctx": 4096, "num_predict": 256, "batch": 1, "temperature": 0.8, "num_gpu": -1},
                "repair_budget": 2,
            },
            "timeout": 180.0,
        }
    ]


def test_cli_generate_release_rejects_runtime_overrides(capsys) -> None:
    exit_code = cli_main.main(["generate", "--task", "Return Lua.", "--num-ctx", "2048"])

    assert exit_code == 2
    assert "release mode does not allow" in capsys.readouterr().err


def test_cli_generate_debug_cloud_model_requires_explicit_flag(capsys) -> None:
    exit_code = cli_main.main(
        [
            "generate",
            "--mode",
            "debug",
            "--task",
            "Return Lua.",
            "--model",
            "gpt-oss:20b-cloud",
        ]
    )

    assert exit_code == 2
    assert "--allow-cloud-model" in capsys.readouterr().err


def test_cli_generate_debug_prints_full_pipeline(monkeypatch, capsys) -> None:
    http_client = SequencedHttpClient(
        [
            {
                "code": "return wf.vars.emails[#wf.vars.emails]",
                "validation_status": "passed",
                "trace": [
                    "request_received",
                    "generation",
                    "format_validation",
                    "rule_validation",
                    "runtime_validation",
                    "semantic_validation",
                    "finalize",
                ],
                "validator_report": {
                    "status": "pass",
                    "iterations": [
                        {
                            "phase": "generation",
                            "runtime_report": {"status": "pass"},
                        }
                    ],
                },
                "critic_report": None,
                "debug": {
                    "prompt_package": {
                        "task_spec": {
                            "operation": "last_array_item",
                            "expected_shape": "scalar_or_nil",
                        },
                        "allowed_data_roots": ["wf.vars.emails"],
                    },
                    "model_calls": [
                        {
                            "phase": "generation",
                            "agent": "generator",
                            "prompt": "PROMPT",
                            "messages": [
                                {"role": "system", "content": "GENERATOR SYSTEM PROMPT"},
                                {"role": "user", "content": "GENERATOR USER PROMPT"},
                            ],
                            "raw_response": "return wf.vars.emails[#wf.vars.emails]",
                        },
                        {
                            "phase": "semantic_validation",
                            "agent": "semantic_critic",
                            "messages": [
                                {"role": "system", "content": "CRITIC SYSTEM PROMPT"},
                                {"role": "user", "content": "CRITIC USER PROMPT"},
                            ],
                            "raw_response": '{"status":"pass"}',
                        }
                    ],
                    "validation_passes": [
                        {
                            "phase": "generation",
                            "runtime_report": {"status": "pass"},
                        }
                    ],
                },
            }
        ]
    )
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)

    exit_code = cli_main.main(
        [
            "generate",
            "--mode",
            "debug",
            "--task",
            "Из полученного списка email получи последний.",
            "--context",
            '{"wf":{"vars":{"emails":["user1@example.com","user2@example.com","user3@example.com"]}}}',
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Pipeline Trace:" in output
    assert "request_received -> generation -> format_validation -> rule_validation -> runtime_validation -> semantic_validation -> finalize" in output
    assert "Request Payload:" in output
    assert '"debug": true' in output
    assert "Prompt Package:" in output
    assert '"operation": "last_array_item"' in output
    assert "Pipeline Layers:" in output
    assert "Agent Layers:" in output
    assert "generation:generator -> semantic_validation:semantic_critic" in output
    assert "Model Calls:" in output
    assert '"phase": "generation"' in output
    assert "GENERATOR SYSTEM PROMPT" in output
    assert "CRITIC SYSTEM PROMPT" in output
    assert "Validation Passes:" in output
    assert '"runtime_report": {' in output
    assert "Critic Report:" in output
    assert "Validator Report:" in output


def test_cli_doctor_checks_api_ollama_and_local_assets(monkeypatch, capsys) -> None:
    http_client = RecordingHttpClient()
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)

    exit_code = cli_main.main(["doctor"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "API" in output
    assert "Ollama" in output
    assert http_client.gets == ["http://127.0.0.1:8011/health", "http://127.0.0.1:11434/api/tags"]


def test_cli_bench_runs_report_script_with_non_cloud_default(monkeypatch) -> None:
    recorded: list[dict[str, object]] = []

    def fake_run(command: list[str], env: dict[str, str], check: bool) -> subprocess.CompletedProcess[str]:
        recorded.append({"command": command, "env": env, "check": check})
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(cli_main.subprocess, "run", fake_run)

    exit_code = cli_main.main(["bench", "--model", "qwen2.5-coder:3b"])

    assert exit_code == 0
    assert recorded[0]["env"]["OLLAMA_MODEL"] == "qwen2.5-coder:3b"


def test_cli_vram_check_writes_report(tmp_path, monkeypatch) -> None:
    http_client = RecordingHttpClient()
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert command[0] == "nvidia-smi"
        assert capture_output is True
        assert text is True
        assert check is True
        return subprocess.CompletedProcess(command, 0, stdout="4096\n")

    monkeypatch.setattr(cli_main.subprocess, "run", fake_run)
    report_path = tmp_path / "VRAM_BENCHMARK.md"

    exit_code = cli_main.main(["vram-check", "--report", str(report_path)])

    assert exit_code == 0
    assert "Peak VRAM: 4.00 GB" in report_path.read_text(encoding="utf-8")


def test_cli_chat_accepts_plain_text_task(monkeypatch, capsys) -> None:
    http_client = RecordingHttpClient()
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)
    commands = iter(["Из массива emails верни последний email.", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    exit_code = cli_main.main(["chat"])

    assert exit_code == 0
    assert "return wf.vars.emails[#wf.vars.emails]" in capsys.readouterr().out
    assert http_client.posts[0]["json"] == {
        "task_text": "Из массива emails верни последний email.",
        "provided_context": None,
        "debug": False,
        "mode": "release",
        "runtime_options": {"num_ctx": 4096, "num_predict": 256, "batch": 1, "temperature": 0.8, "num_gpu": -1},
        "repair_budget": 2,
    }


def test_cli_chat_merges_multiline_json_paste_into_one_task(monkeypatch, capsys) -> None:
    http_client = RecordingHttpClient()
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)
    commands = iter(
        [
            'Из полученного списка email получи последний. {',
            '  "wf": {',
            '    "vars": {',
            '      "emails": ["user1@example.com", "user2@example.com"]',
            "    }",
            "  }",
            "}",
            "/exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    exit_code = cli_main.main(["chat"])

    assert exit_code == 0
    capsys.readouterr()
    assert http_client.posts[0]["json"]["task_text"] == "\n".join(
        [
            'Из полученного списка email получи последний. {',
            '  "wf": {',
            '    "vars": {',
            '      "emails": ["user1@example.com", "user2@example.com"]',
            "    }",
            "  }",
            "}",
        ]
    )


def test_cli_chat_keeps_inline_json_inside_task_text(monkeypatch, capsys) -> None:
    http_client = RecordingHttpClient()
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)
    task_text = (
        'Из полученного списка email получи последний. '
        '{"wf":{"vars":{"emails":["user1@example.com","user2@example.com","user3@example.com"]}}}'
    )
    commands = iter(
        [
            task_text,
            "/exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    exit_code = cli_main.main(["chat"])

    assert exit_code == 0
    assert "return wf.vars.emails[#wf.vars.emails]" in capsys.readouterr().out
    assert http_client.posts[0]["json"] == {
        "task_text": task_text,
        "provided_context": None,
        "debug": False,
        "mode": "release",
        "runtime_options": {"num_ctx": 4096, "num_predict": 256, "batch": 1, "temperature": 0.8, "num_gpu": -1},
        "repair_budget": 2,
    }


def test_cli_chat_context_command_keeps_raw_context_for_next_task(monkeypatch, capsys) -> None:
    http_client = RecordingHttpClient()
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)
    commands = iter(
        [
            '/context { "wf": { "vars": { "emails": [ "user1@example.com", "user2@example.com", "user3@example.com" ] } } }',
            "Из полученного списка email получи последний.",
            "/exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    exit_code = cli_main.main(["chat"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Context updated" in output
    assert "return wf.vars.emails[#wf.vars.emails]" in output
    assert http_client.posts[0]["json"] == {
        "task_text": "Из полученного списка email получи последний.",
        "provided_context": '{ "wf": { "vars": { "emails": [ "user1@example.com", "user2@example.com", "user3@example.com" ] } } }',
        "debug": False,
        "mode": "release",
        "runtime_options": {"num_ctx": 4096, "num_predict": 256, "batch": 1, "temperature": 0.8, "num_gpu": -1},
        "repair_budget": 2,
    }
    assert output.count("Mode: release | Lang: ru | Model: qwen2.5-coder:3b | Path: with-api | Allow cloud: False | Params: num_ctx=4096 num_predict=256 batch=1 temperature=0.8 parallel=1 num_gpu=-1 | Repair budget: 2") == 1


def test_cli_chat_slash_commands_switch_debug_cloud_model(monkeypatch, capsys) -> None:
    http_client = RecordingHttpClient()
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)
    commands = iter(
        [
            "/debug",
            "/allow-cloud on",
            "/model gpt-oss:20b-cloud",
            "Верни Lua print.",
            "/exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    exit_code = cli_main.main(["chat"])

    assert exit_code == 0
    assert "Mode: debug" in capsys.readouterr().out
    assert http_client.posts[0]["json"] == {
        "task_text": "Верни Lua print.",
        "provided_context": None,
        "debug": True,
        "mode": "debug",
        "model": "gpt-oss:20b-cloud",
        "allow_cloud_model": True,
        "repair_budget": 2,
    }


def test_cli_chat_model_n_resets_to_default_and_repair_budget_updates_payload(monkeypatch, capsys) -> None:
    http_client = RecordingHttpClient()
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)
    commands = iter(
        [
            "/debug",
            "/allow-cloud on",
            "/model qwen3-coder:480b-cloud",
            "/ model n",
            "/repair-budget 3",
            "Верни Lua print.",
            "/exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    exit_code = cli_main.main(["chat"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Model: qwen2.5-coder:3b" in output
    assert "Repair budget: 3" in output
    assert http_client.posts[0]["json"] == {
        "task_text": "Верни Lua print.",
        "provided_context": None,
        "debug": True,
        "mode": "debug",
        "allow_cloud_model": True,
        "repair_budget": 3,
    }


def test_cli_chat_debug_prints_full_pipeline(monkeypatch, capsys) -> None:
    http_client = SequencedHttpClient(
        [
            {
                "code": "return wf.vars.emails[#wf.vars.emails]",
                "validation_status": "passed",
                "trace": [
                    "request_received",
                    "generation",
                    "format_validation",
                    "rule_validation",
                    "runtime_validation",
                    "semantic_validation",
                    "finalize",
                ],
                "validator_report": {
                    "status": "pass",
                    "iterations": [
                        {
                            "phase": "generation",
                            "runtime_report": {"status": "pass"},
                        }
                    ],
                },
                "critic_report": None,
                "debug": {
                    "prompt_package": {
                        "task_spec": {
                            "operation": "last_array_item",
                            "expected_shape": "scalar_or_nil",
                        }
                    },
                    "model_calls": [
                        {
                            "phase": "generation",
                            "agent": "generator",
                            "prompt": "PROMPT",
                            "messages": [
                                {"role": "system", "content": "GENERATOR SYSTEM PROMPT"},
                                {"role": "user", "content": "GENERATOR USER PROMPT"},
                            ],
                            "raw_response": "return wf.vars.emails[#wf.vars.emails]",
                        }
                    ],
                    "validation_passes": [
                        {
                            "phase": "generation",
                            "runtime_report": {"status": "pass"},
                        }
                    ],
                },
            }
        ]
    )
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)
    commands = iter(
        [
            "/debug",
            'Из полученного списка email получи последний. {"wf":{"vars":{"emails":["user1@example.com","user2@example.com","user3@example.com"]}}}',
            "/exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert cli_main.main(["chat"]) == 0

    output = capsys.readouterr().out
    assert "Pipeline Trace:" in output
    assert "Debug progress:" in output
    assert "Request Payload:" in output
    assert "Prompt Package:" in output
    assert "Pipeline Layers:" in output
    assert "Agent Layers:" in output
    assert "generation:generator" in output
    assert "Model Calls:" in output
    assert "GENERATOR SYSTEM PROMPT" in output
    assert "Validation Passes:" in output
    assert "Validator Report:" in output


def test_cli_generate_without_api_debug_prints_ollama_request_payload(monkeypatch, capsys) -> None:
    http_client = SequencedHttpClient(
        [
            {
                "response": '{"result":"lua{return wf.vars.emails}lua"}',
            }
        ]
    )
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)

    exit_code = cli_main.main(
        [
            "generate",
            "--without-api",
            "--mode",
            "debug",
            "--task",
            "Из полученного списка email получи последний",
            "--context",
            '{"wf":{"vars":{"emails":["user1@example.com","user2@example.com"]}}}',
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Prompt Package:" in output
    assert "Model Calls:" in output
    assert "http://127.0.0.1:11434/api/generate" in output
    assert '"model": "qwen2.5-coder:3b"' in output
    assert '"num_ctx": 4096' in output
    assert '"num_predict": 256' in output
    assert '"batch": 1' in output
    assert '"temperature": 0.8' in output
    assert "Из полученного списка email получи последний" in output
    assert "wf" in output
    assert "vars" in output
    assert "emails" in output
    assert "user1@example.com" in output
    assert "user2@example.com" in output
    assert "debug payload unavailable" not in output


def test_cli_chat_temperature_command_updates_status_and_direct_ollama_payload(monkeypatch, capsys) -> None:
    http_client = SequencedHttpClient(
        [
            {
                "response": '{"result":"lua{return wf.vars.emails}lua"}',
            }
        ]
    )
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)
    commands = iter(
        [
            "/debug",
            "/without-api",
            "/temperature 0.8",
            "Верни emails.",
            "/exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert cli_main.main(["chat"]) == 0

    output = capsys.readouterr().out
    assert "temperature=0.8" in output
    assert http_client.posts[0]["json"]["options"]["temperature"] == 0.8


def test_cli_chat_lang_command_updates_status_and_payload(monkeypatch, capsys) -> None:
    http_client = RecordingHttpClient()
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)
    commands = iter(
        [
            "/lang en",
            "Return Lua print.",
            "/exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert cli_main.main(["chat"]) == 0

    output = capsys.readouterr().out
    assert "Mode: release | Lang: en" in output
    assert http_client.posts[0]["json"]["language"] == "en"


def test_cli_without_subcommand_starts_chat(monkeypatch) -> None:
    http_client = RecordingHttpClient()
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)
    commands = iter(["/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert cli_main.main([]) == 0


def test_cli_chat_configures_readline_history(tmp_path, monkeypatch) -> None:
    class FakeReadline:
        def __init__(self) -> None:
            self.length: int | None = None
            self.read_paths: list[str] = []
            self.write_paths: list[str] = []
            self.items: list[str] = []

        def set_history_length(self, length: int) -> None:
            self.length = length

        def read_history_file(self, path: str) -> None:
            self.read_paths.append(path)

        def write_history_file(self, path: str) -> None:
            self.write_paths.append(path)

        def get_current_history_length(self) -> int:
            return len(self.items)

        def get_history_item(self, index: int) -> str | None:
            return self.items[index - 1] if index else None

        def add_history(self, line: str) -> None:
            self.items.append(line)

    fake_readline = FakeReadline()
    registered: list[object] = []
    history_path = tmp_path / ".luamts_history"
    history_path.write_text("previous command\n", encoding="utf-8")

    monkeypatch.setattr(cli_main, "_CHAT_HISTORY_CONFIGURED", False)
    monkeypatch.setattr(cli_main, "_CHAT_READLINE", None)
    monkeypatch.setattr(cli_main.importlib, "import_module", lambda name: fake_readline)
    monkeypatch.setattr(cli_main.atexit, "register", lambda callback: registered.append(callback))

    cli_main._configure_chat_history(history_path)
    cli_main._add_chat_history(" /debug ")
    cli_main._add_chat_history("/debug")

    assert fake_readline.length == cli_main.CHAT_HISTORY_LIMIT
    assert fake_readline.read_paths == [str(history_path)]
    assert fake_readline.items == ["/debug"]
    assert len(registered) == 1

    registered[0]()

    assert fake_readline.write_paths == [str(history_path)]


def test_cli_chat_asks_for_feedback_after_bounded_failure(monkeypatch, capsys) -> None:
    http_client = SequencedHttpClient(
        [
            {
                "code": "return wf.vars.emails",
                "validation_status": "bounded_failure",
                "trace": ["request_received", "critic_step", "finalize"],
                "critic_report": {
                    "action": "finalize",
                    "failure_class": "semantic_mismatch",
                    "message": "Repair budget exhausted.",
                },
            },
            {
                "code": "return wf.vars.emails[#wf.vars.emails]",
                "validation_status": "passed",
                "trace": ["request_received", "finalize"],
            },
        ]
    )
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)
    commands = iter(
        [
            'Из полученного списка email получи последний. {"wf":{"vars":{"emails":["user1@example.com","user2@example.com","user3@example.com"]}}}',
            "Нужен последний элемент массива, не весь массив.",
            "/exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    exit_code = cli_main.main(["chat"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Status: bounded_failure" in output
    assert "return wf.vars.emails[#wf.vars.emails]" in output
    assert len(http_client.posts) == 2
    assert http_client.posts[1]["json"]["task_text"] == (
        'Из полученного списка email получи последний. {"wf":{"vars":{"emails":["user1@example.com","user2@example.com","user3@example.com"]}}}\n\n'
        "Обратная связь пользователя после неудачной попытки: Нужен последний элемент массива, не весь массив.\n"
        "Предыдущий кандидат: return wf.vars.emails"
    )


def test_cli_chat_sends_clarification_answer_after_question(monkeypatch, capsys) -> None:
    http_client = SequencedHttpClient(
        [
            {
                "code": "Какой массив использовать: emails или phones?",
                "validation_status": "clarification_requested",
                "trace": ["request_received", "clarification"],
            },
            {
                "code": "return wf.vars.emails[#wf.vars.emails]",
                "validation_status": "passed",
                "trace": ["request_received", "finalize"],
            },
        ]
    )
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)
    commands = iter(
        [
            'Верни последний контакт. {"wf":{"vars":{"emails":["a@example.com"],"phones":["1"]}}}',
            "Используй emails.",
            "/exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    exit_code = cli_main.main(["chat"])

    assert exit_code == 0
    assert "Какой массив использовать" in capsys.readouterr().out
    assert len(http_client.posts) == 2
    assert "Обратная связь пользователя после неудачной попытки: Используй emails." in http_client.posts[1]["json"]["task_text"]


def test_cli_chat_reprints_status_after_parameter_changes(monkeypatch, capsys) -> None:
    http_client = RecordingHttpClient()
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)
    commands = iter(
        [
            "/debug",
            "/allow-cloud on",
            "/model gpt-oss:20b-cloud",
            "/num-predict 512",
            "/status",
            "/exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert cli_main.main(["chat"]) == 0

    output = capsys.readouterr().out
    assert "Mode: debug | Lang: ru | Model: qwen2.5-coder:3b | Path: with-api | Allow cloud: False | Params: num_ctx=4096 num_predict=256 batch=1 temperature=0.8 parallel=1 | Repair budget: 2" in output
    assert "Mode: debug | Lang: ru | Model: gpt-oss:20b-cloud | Path: with-api | Allow cloud: True | Params: num_ctx=4096 num_predict=512 batch=1 temperature=0.8 parallel=1 | Repair budget: 2" in output


def test_cli_chat_context_command_does_not_reprint_status(monkeypatch, capsys) -> None:
    http_client = RecordingHttpClient()
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)
    commands = iter(
        [
            '/context { "wf": { "vars": { "emails": [ "a@example.com" ] } } }',
            "/exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert cli_main.main(["chat"]) == 0

    output = capsys.readouterr().out
    assert output.count("Mode: release | Lang: ru | Model: qwen2.5-coder:3b | Path: with-api | Allow cloud: False | Params: num_ctx=4096 num_predict=256 batch=1 temperature=0.8 parallel=1 num_gpu=-1 | Repair budget: 2") == 1


def test_cli_chat_roots_command_narrows_json_context(monkeypatch, capsys) -> None:
    http_client = RecordingHttpClient()
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)
    commands = iter(
        [
            "/roots wf.vars.emails",
            '/context {"wf":{"vars":{"emails":["a@example.com"],"phones":["1"]},"initVariables":{"token":"secret"}}}',
            "Верни последний email.",
            "/exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert cli_main.main(["chat"]) == 0

    capsys.readouterr()
    assert http_client.posts[0]["json"]["input_roots"] == ["wf.vars.emails"]
    assert http_client.posts[0]["json"]["provided_context"] == '{"wf":{"vars":{"emails":["a@example.com"]}}}'


def test_cli_chat_plan_explains_narrowing_and_feedback(monkeypatch, capsys) -> None:
    http_client = RecordingHttpClient()
    monkeypatch.setattr(cli_main.httpx, "Client", lambda: http_client)
    commands = iter(["/plan", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(commands))

    assert cli_main.main(["chat"]) == 0

    output = capsys.readouterr().out
    assert "explicit roots" in output
    assert "narrow JSON context" in output
    assert "ask for feedback" in output
