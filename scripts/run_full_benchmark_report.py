from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "apps" / "api"
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))
if str(API_ROOT) not in sys.path:
    sys.path.append(str(API_ROOT))

from packages.benchmark.principles import evaluate_case_by_principles  # noqa: E402
from services.generation import GenerationService  # noqa: E402

DATASETS = (
    ("public_cases", REPO_ROOT / "benchmark" / "public_cases.json"),
    ("synthetic_cases", REPO_ROOT / "benchmark" / "synthetic_cases.json"),
    ("lua_tasks_100_cases", REPO_ROOT / "benchmark" / "lua_tasks_100_cases.json"),
    ("lua_tasks_additional_200_cases", REPO_ROOT / "benchmark" / "lua_tasks_additional_200_cases.json"),
)
MODEL = os.environ.get("OLLAMA_MODEL", "qwen3-coder:480b-cloud")
BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")


def _default_output_path(*, now: datetime | None = None, model: str | None = None) -> Path:
    generated_at = now or datetime.now(UTC)
    model_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", model or MODEL).strip("-") or "model"
    return REPO_ROOT / "artifacts" / "benchmark_runs" / (
        f"{generated_at.strftime('%Y%m%dT%H%M%SZ')}_{model_slug}_full-328-report.json"
    )


DEFAULT_OUTPUT_PATH = _default_output_path()
OUTPUT_PATH = Path(os.environ.get("BENCHMARK_REPORT_PATH", str(DEFAULT_OUTPUT_PATH)))


def main() -> None:
    os.environ.setdefault("OLLAMA_MODEL", MODEL)
    os.environ.setdefault("OLLAMA_BASE_URL", BASE_URL)
    os.environ.setdefault("OLLAMA_REQUEST_TIMEOUT", "420")
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    cases = _load_cases()
    service = GenerationService()
    report: dict[str, Any] = {
        "meta": {
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "model": MODEL,
            "base_url": BASE_URL,
            "case_count": len(cases),
            "datasets": [dataset_name for dataset_name, _ in DATASETS],
            "notes": [
                "Results were generated through GenerationService with validation and repair enabled.",
                "This run includes public, synthetic, 100-task holdout, and additional 200-task holdout datasets.",
                "The report is flushed to disk after every case.",
            ],
        },
        "summary": {},
        "cases": [],
    }
    _write_report(report)

    for index, (dataset_name, case) in enumerate(cases, start=1):
        started = time.perf_counter()
        request = _request_from_case(case)
        print(f"[{index}/{len(cases)}] {case['id']} ...", flush=True)

        case_result: dict[str, Any] = {
            "index": index,
            "id": case["id"],
            "dataset": dataset_name,
            "title": case.get("title") or case.get("prompt"),
            "archetype": case["archetype"],
            "output_mode": case["primary_output_mode"],
            "source_ref": case.get("source_ref"),
            "request": request,
            "expected_primary_output": case["expected_outputs"][case["primary_output_mode"]],
        }

        try:
            result = service.generate(**request)
            elapsed = round(time.perf_counter() - started, 2)
            candidate = str(result.get("code", ""))
            principle_evaluation = evaluate_case_by_principles(case, candidate)
            case_result.update(
                {
                    "status": "ok",
                    "elapsed_s": elapsed,
                    "result": result,
                    "principle_evaluation": principle_evaluation,
                }
            )
            print(
                f"[{index}/{len(cases)}] {case['id']} -> {result.get('validation_status')} / principle={principle_evaluation['status']} / {elapsed}s",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 - benchmark reports must capture failures instead of aborting.
            elapsed = round(time.perf_counter() - started, 2)
            case_result.update(
                {
                    "status": "error",
                    "elapsed_s": elapsed,
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                }
            )
            print(f"[{index}/{len(cases)}] {case['id']} -> ERROR {type(exc).__name__}: {exc}", flush=True)

        report["cases"].append(case_result)
        report["summary"] = _build_summary(report["cases"])
        _write_report(report)

    report["meta"]["completed_at_utc"] = datetime.now(UTC).isoformat()
    report["summary"] = _build_summary(report["cases"])
    _write_report(report)
    print(f"saved: {OUTPUT_PATH}", flush=True)


def _load_cases() -> list[tuple[str, dict[str, Any]]]:
    loaded: list[tuple[str, dict[str, Any]]] = []
    for dataset_name, path in DATASETS:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for case in payload["cases"]:
            loaded.append((dataset_name, case))
    return loaded


def _request_from_case(case: dict[str, Any]) -> dict[str, Any]:
    context = json.dumps(case["context"], ensure_ascii=False) if case.get("context") is not None else None
    return {
        "task_text": case["prompt"],
        "provided_context": context,
        "archetype": case["archetype"],
        "output_mode": case["primary_output_mode"],
        "input_roots": case["input_roots"],
        "risk_tags": case["risk_tags"],
        "debug": True,
    }


def _build_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    validation_status_counts: Counter[str] = Counter()
    principle_status_counts: Counter[str] = Counter()
    completed_cases = 0
    error_cases = 0

    for case in cases:
        if case.get("status") != "ok":
            error_cases += 1
            continue

        completed_cases += 1
        validation_status = case.get("result", {}).get("validation_status")
        if validation_status:
            validation_status_counts[str(validation_status)] += 1
        principle_status = case.get("principle_evaluation", {}).get("status")
        if principle_status:
            principle_status_counts[str(principle_status)] += 1

    return {
        "completed_cases": completed_cases,
        "error_cases": error_cases,
        "validation_status_counts": dict(sorted(validation_status_counts.items())),
        "principle_status_counts": dict(sorted(principle_status_counts.items())),
    }


def _write_report(report: dict[str, Any]) -> None:
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
