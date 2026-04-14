from __future__ import annotations

import atexit
import argparse
import importlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from errors import ApiError
from packages.shared.language import DEFAULT_LANGUAGE, VALID_LANGUAGES
from runtime_policy import (
    DEFAULT_MODEL_TAG,
    DEFAULT_PARALLEL,
    RELEASE_MODE,
    RuntimeOptions,
    effective_parallel,
    enforce_model_policy,
    is_cloud_model_tag,
    normalize_mode,
)

try:
    from rich.console import Console
except ModuleNotFoundError:

    class Console:  # type: ignore[no-redef]
        def print(self, *objects: object, **_kwargs: object) -> None:
            print(*objects)


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_API_BASE_URL = os.getenv("LUAMTS_API_BASE_URL", "http://127.0.0.1:8011")
DEFAULT_OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
DEFAULT_TIMEOUT = 180.0
DEFAULT_HISTORY_PATH = Path(os.getenv("LUAMTS_HISTORY_FILE", "~/.luamts_history")).expanduser()
CHAT_HISTORY_LIMIT = 1000
_CHAT_HISTORY_CONFIGURED = False
_CHAT_READLINE: Any | None = None


class CliError(Exception):
    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        return int(args.handler(args))
    except CliError as exc:
        print(exc.message, file=sys.stderr)
        return exc.exit_code
    except ApiError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    except httpx.HTTPError as exc:
        print(f"HTTP request failed: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"Command failed: {' '.join(str(part) for part in exc.cmd)}", file=sys.stderr)
        return exc.returncode or 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="luamts")
    parser.set_defaults(handler=_handle_chat)
    subparsers = parser.add_subparsers(dest="command", required=False)

    generate_parser = subparsers.add_parser("generate")
    _add_runtime_args(generate_parser)
    generate_parser.add_argument("--task", required=True)
    generate_parser.add_argument("--context")
    generate_parser.add_argument("--report", nargs="?", const="")
    generate_parser.add_argument("--debug-trace", action="store_true")
    api_group = generate_parser.add_mutually_exclusive_group()
    api_group.add_argument("--with-api", dest="with_api", action="store_true", default=True)
    api_group.add_argument("--without-api", dest="with_api", action="store_false")
    generate_parser.set_defaults(handler=_handle_generate)

    chat_parser = subparsers.add_parser("chat")
    _add_runtime_args(chat_parser)
    chat_parser.add_argument("--context")
    chat_parser.set_defaults(handler=_handle_chat)

    bench_parser = subparsers.add_parser("bench")
    _add_runtime_args(bench_parser)
    bench_parser.add_argument("--report")
    bench_parser.set_defaults(handler=_handle_bench)

    doctor_parser = subparsers.add_parser("doctor")
    _add_runtime_args(doctor_parser)
    doctor_parser.set_defaults(handler=_handle_doctor)

    vram_parser = subparsers.add_parser("vram-check")
    _add_runtime_args(vram_parser)
    vram_parser.add_argument("--task", default="Return a Lua literal string for smoke testing.")
    vram_parser.add_argument("--report", default=str(REPO_ROOT / "docs" / "VRAM_BENCHMARK.md"))
    vram_parser.set_defaults(handler=_handle_vram_check)

    return parser


def _add_runtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", choices=["release", "debug"], default=RELEASE_MODE)
    parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    parser.add_argument("--ollama-base-url", default=DEFAULT_OLLAMA_BASE_URL)
    parser.add_argument("--model")
    parser.add_argument("--num-ctx", type=int)
    parser.add_argument("--num-predict", type=int)
    parser.add_argument("--batch", type=int)
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--parallel", type=int)
    parser.add_argument("--allow-cloud-model", action="store_true")
    parser.add_argument("--language", choices=list(VALID_LANGUAGES), default=DEFAULT_LANGUAGE)


def _handle_generate(args: argparse.Namespace) -> int:
    _validate_generate_args(args)
    console = Console()
    provided_context = _read_context(args.context)
    if args.with_api:
        response_payload = _generate_with_api(args, provided_context)
    else:
        response_payload = _generate_without_api(args, provided_context)

    status = response_payload.get("validation_status", "not_run")
    code = str(response_payload.get("code", ""))
    console.print(f"Status: {status}")
    _print_literal(console, code)
    _print_pipeline_debug(
        args=args,
        provided_context=provided_context,
        response_payload=response_payload,
        console=console,
    )

    if args.report is not None:
        report_path = Path(args.report) if args.report else _default_report_path(args.mode, _effective_model(args.model))
        _write_json_report(
            report_path,
            {
                "request": _api_request_payload(args, provided_context),
                "response": response_payload,
            },
        )
        console.print(f"Report: {report_path}")
    return 0


def _configure_chat_history(history_path: Path = DEFAULT_HISTORY_PATH) -> None:
    global _CHAT_HISTORY_CONFIGURED, _CHAT_READLINE
    if _CHAT_HISTORY_CONFIGURED:
        return

    try:
        readline = importlib.import_module("readline")
    except ModuleNotFoundError:
        return

    _CHAT_HISTORY_CONFIGURED = True
    _CHAT_READLINE = readline
    try:
        readline.set_history_length(CHAT_HISTORY_LIMIT)
    except AttributeError:
        pass

    try:
        if history_path.exists():
            readline.read_history_file(str(history_path))
    except OSError:
        pass

    def write_history() -> None:
        try:
            history_path.parent.mkdir(parents=True, exist_ok=True)
            readline.write_history_file(str(history_path))
        except OSError:
            pass

    atexit.register(write_history)


def _read_chat_input(prompt: str) -> str:
    line = input(prompt)
    _add_chat_history(line)
    return line


def _add_chat_history(line: str) -> None:
    if _CHAT_READLINE is None:
        return
    history_line = line.strip()
    if not history_line:
        return
    try:
        history_length = _CHAT_READLINE.get_current_history_length()
        if history_length and _CHAT_READLINE.get_history_item(history_length) == history_line:
            return
        _CHAT_READLINE.add_history(history_line)
    except AttributeError:
        pass


def _handle_chat(args: argparse.Namespace) -> int:
    state = _chat_state_from_args(args)
    if sys.stdin.isatty():
        _configure_chat_history()
    console = Console()
    console.print("LocalScript Agent")
    console.print("Type a task as plain text. Use /help for slash commands.")
    _print_chat_status(state, console)

    while True:
        try:
            line = _read_chat_input("luamts> ").strip()
        except EOFError:
            console.print("bye")
            return 0
        if not line:
            continue
        if line.startswith("/"):
            if _apply_chat_command(state, line, console):
                return 0
            continue

        task_text = line
        raw_context = _read_context(state.get("context"))
        input_roots = _explicit_input_roots(state)
        provided_context = _narrow_json_context(raw_context, input_roots)
        request_state = {
            **state,
            "input_roots": input_roots,
            "risk_tags": None,
        }
        task_args = argparse.Namespace(
            **request_state,
            task=task_text,
            report=None,
            debug_trace=state["mode"] == "debug",
            archetype=None,
            output_mode=None,
        )
        try:
            _validate_generate_args(task_args)
            response_payload = (
                _generate_with_api(task_args, provided_context)
                if state["with_api"]
                else _generate_without_api(task_args, provided_context)
            )
        except (CliError, ApiError, httpx.HTTPError) as exc:
            console.print(f"error: {exc}")
            continue

        status = response_payload.get("validation_status", "not_run")
        console.print(f"Status: {status}")
        _print_literal(console, str(response_payload.get("code", "")))
        _print_pipeline_debug(
            args=task_args,
            provided_context=provided_context,
            response_payload=response_payload,
            console=console,
        )
        if _needs_user_feedback(status):
            if _run_feedback_attempt(
                task_args=task_args,
                provided_context=provided_context,
                response_payload=response_payload,
                console=console,
            ):
                return 0


def _chat_state_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "mode": getattr(args, "mode", RELEASE_MODE),
        "api_base_url": getattr(args, "api_base_url", DEFAULT_API_BASE_URL),
        "ollama_base_url": getattr(args, "ollama_base_url", DEFAULT_OLLAMA_BASE_URL),
        "model": getattr(args, "model", None),
        "num_ctx": getattr(args, "num_ctx", None),
        "num_predict": getattr(args, "num_predict", None),
        "batch": getattr(args, "batch", None),
        "temperature": getattr(args, "temperature", None),
        "parallel": getattr(args, "parallel", None),
        "allow_cloud_model": getattr(args, "allow_cloud_model", False),
        "with_api": True,
        "context": getattr(args, "context", None),
        "input_roots": [],
        "language": getattr(args, "language", DEFAULT_LANGUAGE),
    }


def _apply_chat_command(state: dict[str, Any], line: str, console: Console) -> bool:
    parts = line.split()
    command = parts[0].lower()
    values = parts[1:]

    if command in {"/exit", "/quit"}:
        console.print("bye")
        return True
    if command == "/help":
        console.print("Slash commands: /debug, /release, /lang <ru|en>, /model <tag>, /num-ctx <n>, /num-predict <n>, /batch <n>, /temperature <n>, /parallel <n>, /roots <wf.path...>, /allow-cloud on|off, /with-api, /without-api, /context <json-or-path>, /plan, /status, /exit")
        console.print(f"History: use arrow up/down when readline is available; saved to {DEFAULT_HISTORY_PATH}")
        return False
    if command == "/debug":
        state["mode"] = "debug"
        _print_chat_status(state, console)
        return False
    if command == "/release":
        state.update(
            {
                "mode": RELEASE_MODE,
                "model": None,
                "num_ctx": None,
                "num_predict": None,
                "batch": None,
                "temperature": None,
                "parallel": None,
                "allow_cloud_model": False,
                "with_api": True,
            }
        )
        _print_chat_status(state, console)
        return False
    if command == "/temperature":
        if len(values) != 1:
            console.print("usage: /temperature <non-negative-number>")
            return False
        try:
            parsed_value = float(values[0])
        except ValueError:
            console.print("usage: /temperature <non-negative-number>")
            return False
        if parsed_value < 0:
            console.print("usage: /temperature <non-negative-number>")
            return False
        state["temperature"] = parsed_value
        _print_chat_status(state, console)
        return False
    if command == "/model":
        if not values:
            console.print("usage: /model <ollama-tag>")
            return False
        state["model"] = values[0]
        _print_chat_status(state, console)
        return False
    if command in {"/lang", "/language"}:
        if len(values) != 1 or values[0].lower() not in VALID_LANGUAGES:
            console.print("usage: /lang <ru|en>")
            return False
        state["language"] = values[0].lower()
        _print_chat_status(state, console)
        return False
    if command in {"/num-ctx", "/num-predict", "/batch", "/parallel"}:
        if len(values) != 1:
            console.print(f"usage: {command} <positive-int>")
            return False
        try:
            parsed_value = int(values[0])
        except ValueError:
            console.print(f"usage: {command} <positive-int>")
            return False
        if parsed_value <= 0:
            console.print(f"usage: {command} <positive-int>")
            return False
        field_name = command.removeprefix("/").replace("-", "_")
        state[field_name] = parsed_value
        _print_chat_status(state, console)
        return False
    if command == "/roots":
        state["input_roots"] = values
        console.print("Roots: " + (", ".join(values) if values else "auto"))
        return False
    if command == "/plan":
        console.print("Plan: respect explicit roots -> narrow JSON context -> generate -> validate/repair -> ask for feedback when bounded.")
        return False
    if command == "/allow-cloud":
        if not values or values[0].lower() not in {"on", "off"}:
            console.print("usage: /allow-cloud on|off")
            return False
        state["allow_cloud_model"] = values[0].lower() == "on"
        _print_chat_status(state, console)
        return False
    if command == "/with-api":
        state["with_api"] = True
        _print_chat_status(state, console)
        return False
    if command == "/without-api":
        state["with_api"] = False
        _print_chat_status(state, console)
        return False
    if command == "/context":
        state["context"] = " ".join(values) if values else None
        console.print("Context updated" if state["context"] else "Context cleared")
        return False
    if command == "/status":
        _print_chat_status(state, console)
        return False

    console.print(f"unknown command: {command}")
    return False


def _print_chat_status(state: dict[str, Any], console: Console) -> None:
    model = state["model"] or _effective_model(None)
    path = "with-api" if state["with_api"] else "without-api"
    options = _runtime_options_from_args(argparse.Namespace(**state))
    parallel = state["parallel"] or effective_parallel()
    console.print(
        f"Mode: {state['mode']} | Lang: {state['language']} | Model: {model} | Path: {path} | "
        f"Allow cloud: {state['allow_cloud_model']} | Params: {_params_label(options, parallel)}"
    )


def _needs_user_feedback(status: object) -> bool:
    return str(status) in {"bounded_failure", "validator_conflict", "clarification_requested"}


def _run_feedback_attempt(
    *,
    task_args: argparse.Namespace,
    provided_context: str | None,
    response_payload: dict[str, object],
    console: Console,
) -> bool:
    if response_payload.get("critic_report"):
        critic_report = response_payload["critic_report"]
        if isinstance(critic_report, dict):
            message = critic_report.get("message")
            if message:
                _print_literal(console, f"Critic: {message}")

    feedback = _read_chat_input("feedback> ").strip()
    if not feedback:
        console.print("Feedback skipped.")
        return False
    if feedback.lower() in {"/exit", "/quit"}:
        console.print("bye")
        return True

    feedback_args = argparse.Namespace(**vars(task_args))
    feedback_args.task = _task_with_feedback(
        original_task=str(task_args.task),
        feedback=feedback,
        previous_candidate=str(response_payload.get("code", "")),
        language=str(getattr(task_args, "language", DEFAULT_LANGUAGE)),
    )
    retry_payload = (
        _generate_with_api(feedback_args, provided_context)
        if task_args.with_api
        else _generate_without_api(feedback_args, provided_context)
    )
    status = retry_payload.get("validation_status", "not_run")
    console.print(f"Status: {status}")
    _print_literal(console, str(retry_payload.get("code", "")))
    _print_pipeline_debug(
        args=feedback_args,
        provided_context=provided_context,
        response_payload=retry_payload,
        console=console,
    )
    return False


def _task_with_feedback(*, original_task: str, feedback: str, previous_candidate: str, language: str) -> str:
    feedback_label = "Обратная связь пользователя после неудачной попытки" if language == "ru" else "User feedback after failed attempt"
    previous_label = "Предыдущий кандидат" if language == "ru" else "Previous candidate"
    return "\n".join(
        [
            original_task,
            "",
            f"{feedback_label}: {feedback}",
            f"{previous_label}: {previous_candidate}",
        ]
    ).strip()


def _print_pipeline_debug(
    *,
    args: argparse.Namespace,
    provided_context: str | None,
    response_payload: dict[str, object],
    console: Console,
) -> None:
    if not (args.mode == "debug" or bool(getattr(args, "debug_trace", False))):
        return

    console.print("Pipeline Trace:")
    trace = response_payload.get("trace")
    if isinstance(trace, list) and trace:
        console.print(" -> ".join(str(item) for item in trace))
    else:
        console.print("trace unavailable")

    console.print("Request Payload:")
    _print_literal(console, _pretty_json(_debug_request_payload(args, provided_context)))

    debug_payload = response_payload.get("debug")
    if isinstance(debug_payload, dict):
        console.print("Prompt Package:")
        _print_literal(console, _pretty_json(debug_payload.get("prompt_package")))

        console.print("Pipeline Layers:")
        _print_literal(console, _pretty_json(debug_payload.get("pipeline_layers", [])))

        console.print("Agent Layers:")
        _print_literal(console, _format_agent_layers(debug_payload))

        console.print("Agent Layer Calls:")
        _print_literal(console, _pretty_json(debug_payload.get("agent_layer_calls", [])))

        console.print("Model Calls:")
        _print_literal(console, _pretty_json(debug_payload.get("model_calls", [])))

        console.print("Validation Passes:")
        _print_literal(console, _pretty_json(debug_payload.get("validation_passes", [])))
    else:
        console.print("Debug Payload:")
        console.print("debug payload unavailable")

    console.print("Critic Report:")
    _print_literal(console, _pretty_json(response_payload.get("critic_report")))

    console.print("Validator Report:")
    _print_literal(console, _pretty_json(response_payload.get("validator_report")))


def _format_agent_layers(debug_payload: dict[str, object]) -> str:
    agent_layer_calls = debug_payload.get("agent_layer_calls")
    model_calls = debug_payload.get("model_calls")
    combined_calls: list[object] = []
    if isinstance(agent_layer_calls, list):
        combined_calls.extend(agent_layer_calls)
    if isinstance(model_calls, list):
        combined_calls.extend(model_calls)
    if not combined_calls:
        return "agent layers unavailable"

    layers: list[str] = []
    for call in combined_calls:
        if not isinstance(call, dict):
            continue
        phase = str(call.get("phase") or "unknown_phase")
        agent = str(call.get("agent") or "unknown_agent")
        layers.append(f"{phase}:{agent}")
    return " -> ".join(layers) if layers else "agent layers unavailable"


def _debug_request_payload(args: argparse.Namespace, provided_context: str | None) -> dict[str, object]:
    if getattr(args, "with_api", False):
        return _api_request_payload(args, provided_context)
    payload: dict[str, object] = {
        "task_text": args.task,
        "provided_context": provided_context,
        "mode": args.mode,
        "debug": args.mode == "debug" or bool(getattr(args, "debug_trace", False)),
        "model": _effective_model(args.model),
    }
    runtime_options = _runtime_options_payload_from_args(args)
    if runtime_options is not None:
        payload["runtime_options"] = runtime_options
    return payload


def _pretty_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _print_literal(console: Console, value: object) -> None:
    console.print(str(value), markup=False)


def _handle_doctor(args: argparse.Namespace) -> int:
    model = _effective_model(args.model)
    enforce_model_policy(model, mode=args.mode, allow_cloud_model=args.allow_cloud_model)
    options = _runtime_options_from_args(args)
    parallel = args.parallel or effective_parallel()
    checks: list[tuple[str, bool, str]] = []

    with httpx.Client() as client:
        checks.append(("API", _http_ok(client, f"{args.api_base_url.rstrip('/')}/health"), args.api_base_url))
        checks.append(("Ollama", _http_ok(client, f"{args.ollama_base_url.rstrip('/')}/api/tags"), args.ollama_base_url))

    for label, path in _local_asset_paths():
        checks.append((label, path.exists(), str(path.relative_to(REPO_ROOT))))

    cloud_allowed = not is_cloud_model_tag(model) or (args.mode == "debug" and args.allow_cloud_model)
    checks.append(("cloud model guard", cloud_allowed, model))
    checks.append(("runtime params", True, _params_label(options, parallel)))

    console = Console()
    all_ok = True
    for name, ok, detail in checks:
        all_ok = all_ok and ok
        console.print(f"{name}: {'ok' if ok else 'fail'} - {detail}")
    return 0 if all_ok else 1


def _handle_bench(args: argparse.Namespace) -> int:
    model = _effective_model(args.model)
    enforce_model_policy(model, mode=args.mode, allow_cloud_model=args.allow_cloud_model)
    report_path = args.report or str(_default_report_path(args.mode, model, suffix="full-report"))
    env = os.environ.copy()
    env.update(
        {
            "OLLAMA_MODEL": model,
            "OLLAMA_BASE_URL": args.ollama_base_url,
            "BENCHMARK_REPORT_PATH": report_path,
        }
    )
    command = [sys.executable, str(REPO_ROOT / "scripts" / "run_full_benchmark_report.py")]
    completed = subprocess.run(command, env=env, check=True)
    return completed.returncode


def _handle_vram_check(args: argparse.Namespace) -> int:
    model = _effective_model(args.model)
    enforce_model_policy(model, mode=args.mode, allow_cloud_model=args.allow_cloud_model)
    options = RuntimeOptions()
    with httpx.Client() as client:
        client.post(
            f"{args.api_base_url.rstrip('/')}/generate",
            json={
                "task_text": args.task,
                "debug": False,
                "mode": RELEASE_MODE,
                "model": model,
                "runtime_options": options.to_ollama_options(),
            },
            timeout=DEFAULT_TIMEOUT,
        ).raise_for_status()

    peak_gb = _measure_peak_vram_gb()
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "\n".join(
            [
                "# VRAM Benchmark",
                "",
                f"- Model: `{model}`",
                f"- Params: `{_params_label(options, DEFAULT_PARALLEL)}`",
                f"- Peak VRAM: {peak_gb:.2f} GB",
                "",
            ]
        ),
        encoding="utf-8",
    )
    if peak_gb > 8.0:
        print(f"Peak VRAM {peak_gb:.2f} GB exceeds 8.0 GB.", file=sys.stderr)
        return 1
    Console().print(f"Peak VRAM: {peak_gb:.2f} GB")
    return 0


def _validate_generate_args(args: argparse.Namespace) -> None:
    normalize_mode(args.mode)
    if args.mode == RELEASE_MODE:
        blocked = []
        for flag_name, value in (
            ("--model", args.model),
            ("--num-ctx", args.num_ctx),
            ("--num-predict", args.num_predict),
            ("--batch", args.batch),
            ("--temperature", args.temperature),
            ("--parallel", args.parallel),
        ):
            if value is not None:
                blocked.append(flag_name)
        if args.allow_cloud_model:
            blocked.append("--allow-cloud-model")
        if not args.with_api:
            blocked.append("--without-api")
        if blocked:
            raise CliError(f"release mode does not allow these flags: {', '.join(blocked)}")
    else:
        enforce_model_policy(
            _effective_model(args.model),
            mode=args.mode,
            allow_cloud_model=args.allow_cloud_model,
        )


def _generate_with_api(args: argparse.Namespace, provided_context: str | None) -> dict[str, object]:
    payload = _api_request_payload(args, provided_context)
    with httpx.Client() as client:
        response = client.post(f"{args.api_base_url.rstrip('/')}/generate", json=payload, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        return dict(response.json())


def _generate_without_api(args: argparse.Namespace, provided_context: str | None) -> dict[str, object]:
    prompt_parts = [args.task]
    if provided_context:
        prompt_parts.append(provided_context)
    prompt = "\n\n".join(prompt_parts)
    options = _runtime_options_from_args(args)
    ollama_url = f"{args.ollama_base_url.rstrip('/')}/api/generate"
    request_payload = {
        "model": _effective_model(args.model),
        "prompt": prompt,
        "stream": False,
        "options": options.to_ollama_options(),
    }
    with httpx.Client() as client:
        response = client.post(
            ollama_url,
            json=request_payload,
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    raw_response = str(payload["response"])
    response_payload: dict[str, object] = {
        "code": raw_response,
        "validation_status": "not_run",
        "trace": ["request_received", "direct_ollama", "response_ready"],
    }
    if args.mode == "debug" or bool(getattr(args, "debug_trace", False)):
        response_payload["debug"] = {
            "prompt_package": {
                "prompt": prompt,
            },
            "pipeline_layers": [
                {
                    "stage": "direct_ollama",
                    "kind": "llm_prompt",
                    "status": "completed",
                    "agent": "direct_ollama",
                },
            ],
            "agent_layer_calls": [],
            "model_calls": [
                {
                    "phase": "generation",
                    "agent": "direct_ollama",
                    "url": ollama_url,
                    "request_payload": request_payload,
                    "raw_response": raw_response,
                }
            ],
            "validation_passes": [],
        }
    return response_payload


def _api_request_payload(args: argparse.Namespace, provided_context: str | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "task_text": args.task,
        "provided_context": provided_context,
        "debug": args.mode == "debug" or bool(getattr(args, "debug_trace", False)),
        "mode": args.mode,
    }
    if getattr(args, "language", DEFAULT_LANGUAGE) != DEFAULT_LANGUAGE:
        payload["language"] = getattr(args, "language")
    for field_name in ("archetype", "output_mode", "input_roots", "risk_tags"):
        field_value = getattr(args, field_name, None)
        if field_value is not None:
            payload[field_name] = field_value
    if args.model is not None:
        payload["model"] = args.model
    runtime_options = _runtime_options_payload_from_args(args)
    if runtime_options is not None:
        payload["runtime_options"] = runtime_options
    if args.allow_cloud_model:
        payload["allow_cloud_model"] = True
    return payload


def _runtime_options_payload_from_args(args: argparse.Namespace) -> dict[str, int | float] | None:
    if args.num_ctx is None and args.num_predict is None and args.batch is None and args.temperature is None:
        return None
    options = _runtime_options_from_args(args)
    return options.to_ollama_options()


def _runtime_options_from_args(args: argparse.Namespace) -> RuntimeOptions:
    defaults = RuntimeOptions.from_env()
    return RuntimeOptions(
        num_ctx=args.num_ctx or defaults.num_ctx,
        num_predict=args.num_predict or defaults.num_predict,
        batch=args.batch or defaults.batch,
        temperature=defaults.temperature if args.temperature is None else args.temperature,
    )


def _read_context(raw_context: str | None) -> str | None:
    if raw_context is None:
        return None
    context_path = Path(raw_context)
    if context_path.exists():
        return context_path.read_text(encoding="utf-8").strip()
    try:
        json.loads(raw_context)
    except json.JSONDecodeError as exc:
        raise CliError("--context must be inline JSON or a path to a JSON file.") from exc
    return raw_context


def _explicit_input_roots(state: dict[str, Any]) -> list[str] | None:
    explicit_roots = [str(root) for root in state.get("input_roots", []) if str(root).strip()]
    return explicit_roots or None


def _infer_input_roots_from_context(task_text: str, provided_context: str | None) -> list[str]:
    roots = _json_leaf_roots(provided_context)
    if not roots:
        return []
    task_tokens = _normalized_task_tokens(task_text)
    matched_roots: list[str] = []
    for root in roots:
        leaf_name = root.rsplit(".", 1)[-1].lower()
        singular_leaf_name = leaf_name[:-1] if leaf_name.endswith("s") else leaf_name
        if leaf_name in task_tokens or singular_leaf_name in task_tokens:
            matched_roots.append(root)
    return matched_roots


def _infer_risk_tags(task_text: str, input_roots: list[str] | None) -> list[str] | None:
    lowered = task_text.lower()
    risk_tags: list[str] = []
    if input_roots and (
        "послед" in lowered
        or "first" in lowered
        or "last" in lowered
        or "перв" in lowered
    ):
        risk_tags.extend(["array_indexing", "empty_array"])
    return risk_tags or None


def _narrow_json_context(provided_context: str | None, input_roots: list[str] | None) -> str | None:
    if not provided_context or not input_roots:
        return provided_context
    try:
        payload = json.loads(provided_context)
    except json.JSONDecodeError:
        return provided_context
    if not isinstance(payload, dict):
        return provided_context

    narrowed: dict[str, Any] = {}
    for root in input_roots:
        value_found, value = _get_nested_value(payload, root.split("."))
        if value_found:
            _set_nested_value(narrowed, root.split("."), value)
    if not narrowed:
        return provided_context
    return json.dumps(narrowed, ensure_ascii=False, separators=(",", ":"))


def _json_leaf_roots(provided_context: str | None) -> list[str]:
    if not provided_context:
        return []
    try:
        payload = json.loads(provided_context)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    roots: list[str] = []
    _collect_leaf_roots(payload, [], roots)
    return roots


def _collect_leaf_roots(node: object, path: list[str], roots: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            _collect_leaf_roots(value, [*path, str(key)], roots)
        return
    if path:
        roots.append(".".join(path))


def _get_nested_value(payload: dict[str, Any], parts: list[str]) -> tuple[bool, Any]:
    current: Any = payload
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _set_nested_value(payload: dict[str, Any], parts: list[str], value: Any) -> None:
    current = payload
    for part in parts[:-1]:
        next_node = current.get(part)
        if not isinstance(next_node, dict):
            next_node = {}
            current[part] = next_node
        current = next_node
    current[parts[-1]] = value


def _normalized_task_tokens(task_text: str) -> set[str]:
    normalized = "".join(char.lower() if char.isalnum() else " " for char in task_text)
    return {token for token in normalized.split() if token}


def _infer_chat_archetype(task_text: str) -> str:
    lowered = task_text.lower()
    extraction_markers = (
        "верни",
        "получи",
        "получить",
        "последний",
        "последнюю",
        "last",
        "return",
        "get ",
    )
    mutation_markers = (
        "lua",
        "print",
        "очист",
        "измени",
        "замени",
        "добав",
        "удали",
        "отфильтр",
        "filter",
        "replace",
        "remove",
        "add ",
    )
    if any(marker in lowered for marker in extraction_markers) and not any(
        marker in lowered for marker in mutation_markers
    ):
        return "simple_extraction"
    return "transformation"


def _http_ok(client: httpx.Client, url: str) -> bool:
    try:
        response = client.get(url, timeout=10.0)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def _measure_peak_vram_gb() -> float:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise CliError("nvidia-smi is required for vram-check.", exit_code=1) from exc
    readings = [int(line.strip()) for line in result.stdout.splitlines() if line.strip()]
    if not readings:
        raise CliError("nvidia-smi returned no memory readings.", exit_code=1)
    return max(readings) / 1024


def _effective_model(model: str | None) -> str:
    return model or os.getenv("OLLAMA_MODEL", DEFAULT_MODEL_TAG)


def _default_report_path(mode: str, model: str, *, suffix: str = "generate-report") -> Path:
    generated_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    model_slug = "".join(char if char.isalnum() or char in "._-" else "-" for char in model).strip("-")
    return REPO_ROOT / "artifacts" / "benchmark_runs" / f"{generated_at}_{model_slug}_{mode}_{suffix}.json"


def _write_json_report(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _local_asset_paths() -> list[tuple[str, Path]]:
    return [
        ("templates", REPO_ROOT / "knowledge" / "templates" / "domain_prompt_templates.json"),
        ("examples", REPO_ROOT / "knowledge" / "examples"),
        ("archetypes", REPO_ROOT / "knowledge" / "archetypes"),
    ]


def _params_label(options: RuntimeOptions, parallel: int) -> str:
    return (
        f"num_ctx={options.num_ctx} num_predict={options.num_predict} "
        f"batch={options.batch} temperature={options.temperature:g} parallel={parallel}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
