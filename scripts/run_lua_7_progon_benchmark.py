from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path(r"C:\Users\epitaph\Downloads\lua_benchmark_tasks_300_with_hints_and_solutions.txt")
DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / "benchmark_runs" / "7_progon"
TASK_SEPARATOR = "=" * 50
HEADER_PATTERN = re.compile(r"^ЗАДАЧА\s+(?P<number>\d{3})\nКатегория:\s*(?P<category>[^\n]+)\n")
SUCCESS_STATUSES = {"passed", "repaired"}


@dataclass(frozen=True)
class BenchmarkCase:
    task_id: str
    category: str
    prompt: str
    context: str
    return_description: str
    hint: str
    solution: str


def main() -> None:
    args = _parse_args()
    source_path = Path(args.source)
    output_dir = _resolve_output_dir(Path(args.output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)

    cases = parse_source_text(source_path.read_text(encoding="utf-8", errors="replace"))
    selected_cases = select_interesting_cases(cases, limit=args.limit)
    selected_path = output_dir / "selected_tasks.json"
    ndjson_path = output_dir / "run.ndjson"
    report_path = output_dir / "run.json"
    summary_path = output_dir / "summary.txt"

    selected_path.write_text(
        json.dumps([_selected_case_payload(case) for case in selected_cases], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    report: dict[str, Any] = {
        "meta": {
            "run_name": "7_progon",
            "started_at_utc": datetime.now(UTC).isoformat(),
            "source": str(source_path),
            "api_url": args.api_url,
            "model": args.model,
            "mode": args.mode,
            "allow_cloud_model": args.allow_cloud,
            "runtime_options": _runtime_options(args),
            "repair_budget": args.repair_budget,
            "selected_count": len(selected_cases),
            "parsed_count": len(cases),
            "notes": [
                "Each case is attempted without the strong hint first.",
                "If validation does not pass, the same case is retried once with the strong hint.",
                "The expected solution is logged for analysis only and is never sent to the API.",
            ],
        },
        "summary": {},
        "cases": [],
    }
    _write_report(report_path, report)
    ndjson_path.write_text("", encoding="utf-8")

    if args.dry_run:
        report["meta"]["completed_at_utc"] = datetime.now(UTC).isoformat()
        report["summary"] = _build_summary(report["cases"])
        _write_report(report_path, report)
        summary_path.write_text(_format_summary(report), encoding="utf-8")
        print(f"dry-run: parsed {len(cases)}, selected {len(selected_cases)}", flush=True)
        print(f"selected: {selected_path}", flush=True)
        return

    timeout = httpx.Timeout(args.timeout)
    with httpx.Client(timeout=timeout) as client:
        for index, case in enumerate(selected_cases, start=1):
            started = time.perf_counter()
            print(f"[{index:02d}/{len(selected_cases)}] task {case.task_id} ...", flush=True)
            final_result = _run_case(client, args, case)
            final_result["index"] = index
            final_result["elapsed_s"] = round(time.perf_counter() - started, 2)

            report["cases"].append(final_result)
            report["summary"] = _build_summary(report["cases"])
            _write_report(report_path, report)
            _append_ndjson(ndjson_path, final_result)
            print(_format_case_line(index, len(selected_cases), final_result), flush=True)

    report["meta"]["completed_at_utc"] = datetime.now(UTC).isoformat()
    report["summary"] = _build_summary(report["cases"])
    _write_report(report_path, report)
    summary_path.write_text(_format_summary(report), encoding="utf-8")
    print(f"saved: {report_path}", flush=True)
    print(_format_summary(report), flush=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run 7_progon Lua benchmark through the local API.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--api-url", default="http://127.0.0.1:8011")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--model", default="qwen3-coder:480b-cloud")
    parser.add_argument("--mode", default="debug")
    parser.add_argument("--allow-cloud", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--num-predict", type=int, default=256)
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--repair-budget", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def parse_source_text(text: str) -> list[BenchmarkCase]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = normalized.split(f"\n{TASK_SEPARATOR}\n")
    cases: list[BenchmarkCase] = []
    for part in parts:
        block = part.strip()
        if not block.startswith("ЗАДАЧА "):
            continue
        cases.append(_parse_case_block(block))
    return cases


def _parse_case_block(block: str) -> BenchmarkCase:
    match = HEADER_PATTERN.match(block)
    if match is None:
        raise ValueError(f"Failed to parse task header:\n{block[:400]}")
    return BenchmarkCase(
        task_id=match.group("number"),
        category=match.group("category").strip(),
        prompt=_extract_section(block, "Запрос пользователя:\n", "\n\nКонтекст:\n"),
        context=_extract_section(block, "Контекст:\n", "\n\nЧто нужно вернуть:\n"),
        return_description=_extract_section(block, "Что нужно вернуть:\n", "\n\nСильная подсказка:\n"),
        hint=_extract_section(block, "Сильная подсказка:\n", "\n\nОжидаемое решение (Lua):\n"),
        solution=_extract_section(block, "Ожидаемое решение (Lua):\n", None),
    )


def _extract_section(block: str, start_marker: str, end_marker: str | None) -> str:
    start_index = block.index(start_marker) + len(start_marker)
    if end_marker is None:
        return block[start_index:].strip()
    end_index = block.index(end_marker, start_index)
    return block[start_index:end_index].strip()


def select_interesting_cases(cases: list[BenchmarkCase], *, limit: int) -> list[BenchmarkCase]:
    scored_by_category: dict[str, list[tuple[int, BenchmarkCase]]] = defaultdict(list)
    for case in cases:
        scored_by_category[case.category].append((interesting_score(case), case))
    for category_cases in scored_by_category.values():
        category_cases.sort(key=lambda item: (-item[0], int(item[1].task_id)))

    category_order = sorted(
        scored_by_category,
        key=lambda category: (-scored_by_category[category][0][0], category),
    )
    selected: list[BenchmarkCase] = []
    seen: set[str] = set()
    while len(selected) < limit:
        added_in_round = False
        for category in category_order:
            bucket = scored_by_category[category]
            if not bucket:
                continue
            _, case = bucket.pop(0)
            if case.task_id in seen:
                continue
            selected.append(case)
            seen.add(case.task_id)
            added_in_round = True
            if len(selected) >= limit:
                break
        if not added_in_round:
            break
    return selected


def interesting_score(case: BenchmarkCase) -> int:
    text = "\n".join([case.category, case.prompt, case.hint, case.solution]).casefold()
    solution = case.solution
    score = min(20, len([line for line in solution.splitlines() if line.strip()]))

    category_markers = {
        "агрегирование по группам": 18,
        "влож": 16,
        "разворачив": 15,
        "нормализац": 14,
        "валидац": 13,
        "матриц": 13,
        "дата": 12,
        "время": 11,
        "преобразование структуры": 11,
        "разбор строк": 10,
        "уник": 9,
        "фильтрац": 8,
        "агрегирование": 8,
    }
    for marker, weight in category_markers.items():
        if marker in text:
            score += weight

    token_weights = {
        "_utils.array.new()": 10,
        "_utils.array.markasarray": 8,
        "wf.initvariables": 8,
        "string.format": 7,
        ":gmatch": 9,
        ":match": 6,
        ":gsub": 6,
        ":sub": 5,
        "tonumber": 5,
        "type(": 5,
        "pairs(": 4,
        "ipairs(": 4,
        "os.": 8,
        "nil": 3,
        " = nil": 5,
        "is_leap": 8,
    }
    solution_lower = solution.casefold()
    for token, weight in token_weights.items():
        if token in solution_lower:
            score += weight
    if solution_lower.count("for ") >= 2:
        score += 10
    if solution_lower.count("if ") >= 3:
        score += 5
    return score


def build_task_text(case: BenchmarkCase, *, include_hint: bool) -> str:
    sections = [
        case.prompt,
        "",
        "Что нужно вернуть:",
        case.return_description,
    ]
    if include_hint:
        sections.extend(["", "Сильная подсказка:", case.hint])
    return "\n".join(sections).strip()


def _run_case(client: httpx.Client, args: argparse.Namespace, case: BenchmarkCase) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    base_attempt = _run_attempt(client, args, case, include_hint=False)
    attempts.append(base_attempt)
    if base_attempt["success"]:
        return _case_result(case, attempts, final_attempt=base_attempt)

    hint_attempt = _run_attempt(client, args, case, include_hint=True)
    attempts.append(hint_attempt)
    return _case_result(case, attempts, final_attempt=hint_attempt)


def _run_attempt(
    client: httpx.Client,
    args: argparse.Namespace,
    case: BenchmarkCase,
    *,
    include_hint: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    payload = {
        "task_text": build_task_text(case, include_hint=include_hint),
        "provided_context": case.context,
        "debug": True,
        "mode": args.mode,
        "model": args.model,
        "allow_cloud_model": args.allow_cloud,
        "runtime_options": _runtime_options(args),
        "language": "ru",
        "repair_budget": args.repair_budget,
    }
    try:
        response = client.post(f"{args.api_url.rstrip('/')}/generate", json=payload)
        response.raise_for_status()
        response_payload = response.json()
    except Exception as exc:  # noqa: BLE001 - benchmark must log failures and continue.
        return {
            "attempt": "with_hint" if include_hint else "base",
            "success": False,
            "elapsed_s": round(time.perf_counter() - started, 2),
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }

    validation_status = str(response_payload.get("validation_status") or "")
    repair_count = int(response_payload.get("repair_count") or 0)
    return {
        "attempt": "with_hint" if include_hint else "base",
        "success": validation_status in SUCCESS_STATUSES,
        "elapsed_s": round(time.perf_counter() - started, 2),
        "validation_status": validation_status,
        "stop_reason": response_payload.get("stop_reason"),
        "repair_count": repair_count,
        "passed_on_generation": repair_count + 1 if validation_status in SUCCESS_STATUSES else None,
        "trace": response_payload.get("trace", []),
        "code": response_payload.get("code"),
        "critic_report": response_payload.get("critic_report"),
        "validator_report": response_payload.get("validator_report"),
    }


def _case_result(case: BenchmarkCase, attempts: list[dict[str, Any]], *, final_attempt: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": case.task_id,
        "category": case.category,
        "interesting_score": interesting_score(case),
        "status": "passed" if final_attempt["success"] else "failed",
        "passed_with_hint": final_attempt["success"] and final_attempt["attempt"] == "with_hint",
        "benchmark_attempts_used": len(attempts),
        "passed_on_generation": final_attempt.get("passed_on_generation"),
        "prompt": case.prompt,
        "return_description": case.return_description,
        "hint": case.hint,
        "expected_solution": case.solution,
        "attempts": attempts,
    }


def _runtime_options(args: argparse.Namespace) -> dict[str, int | float]:
    return {
        "num_ctx": args.num_ctx,
        "num_predict": args.num_predict,
        "batch": args.batch,
        "temperature": args.temperature,
    }


def _build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(str(result.get("status")) for result in results)
    category_counts = Counter(str(result.get("category")) for result in results)
    passed_with_hint = sum(1 for result in results if result.get("passed_with_hint"))
    passed_without_hint = sum(
        1 for result in results if result.get("status") == "passed" and not result.get("passed_with_hint")
    )
    passed_on_generation_counts = Counter(
        str(result.get("passed_on_generation"))
        for result in results
        if result.get("status") == "passed" and result.get("passed_on_generation") is not None
    )
    return {
        "total": len(results),
        "status_counts": dict(sorted(status_counts.items())),
        "passed_without_hint": passed_without_hint,
        "passed_with_hint": passed_with_hint,
        "passed_on_generation_counts": dict(sorted(passed_on_generation_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
    }


def _selected_case_payload(case: BenchmarkCase) -> dict[str, Any]:
    payload = asdict(case)
    payload["interesting_score"] = interesting_score(case)
    return payload


def _format_case_line(index: int, total: int, result: dict[str, Any]) -> str:
    status = result.get("status")
    hint = "hint" if result.get("passed_with_hint") else "base"
    generation = result.get("passed_on_generation") or "-"
    elapsed = result.get("elapsed_s")
    return (
        f"[{index:02d}/{total}] task {result.get('task_id')} -> {status} "
        f"attempt={hint} gen={generation} elapsed={elapsed}s"
    )


def _format_summary(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    return "\n".join(
        [
            "7_progon benchmark summary",
            f"total: {summary.get('total', 0)}",
            f"status_counts: {summary.get('status_counts', {})}",
            f"passed_without_hint: {summary.get('passed_without_hint', 0)}",
            f"passed_with_hint: {summary.get('passed_with_hint', 0)}",
            f"passed_on_generation_counts: {summary.get('passed_on_generation_counts', {})}",
        ]
    )


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_ndjson(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _resolve_output_dir(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise
