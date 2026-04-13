from __future__ import annotations

import json
import re
from argparse import ArgumentParser
from collections import Counter
from pathlib import Path

_TASK_SEPARATOR = "=" * 50
_ROOT_PATTERN = re.compile(r"wf\.(?:vars|initVariables)\.[A-Za-z0-9_\.]+")
_ARITHMETIC_PATTERN = re.compile(r"\s[+\-*/%]\s")
_INDEXING_PATTERN = re.compile(r"\[(?:#\w+|\d+)\]")
_WHITESPACE_NORMALIZER = re.compile(r"\s+")
_HEADER_PATTERN = re.compile(r"^ЗАДАЧА\s+(?P<number>\d{3})\nКатегория:\s*(?P<category>[^\n]+)\n")


def _parse_args() -> ArgumentParser:
    parser = ArgumentParser(description="Convert a Lua benchmark text file into benchmark JSON.")
    parser.add_argument(
        "--source",
        default=r"C:\Users\epitaph\Downloads\lua_benchmark_tasks_100_with_solutions.txt",
        help="Path to the source text file.",
    )
    parser.add_argument(
        "--output",
        default="benchmark/lua_tasks_100_cases.json",
        help="Where to write the generated benchmark JSON.",
    )
    parser.add_argument(
        "--expected-count",
        type=int,
        default=100,
        help="Expected number of parsed tasks.",
    )
    parser.add_argument(
        "--dataset-id",
        default="localscript-lua-tasks-100-holdout",
        help="Dataset id to store in the generated JSON.",
    )
    parser.add_argument(
        "--id-prefix",
        default="lua100-task-",
        help="Case id prefix; the task number is appended to it.",
    )
    parser.add_argument(
        "--source-id",
        default="lua_benchmark_tasks_100_txt",
        help="Source reference id to store in dataset metadata and each case.",
    )
    parser.add_argument(
        "--source-note",
        default="External holdout benchmark. Do not move these cases into knowledge/examples.",
        help="Source note to store in dataset metadata.",
    )
    return parser


def _split_task_blocks(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = normalized.split(f"\n{_TASK_SEPARATOR}\n")
    return [part.strip() for part in parts if part.strip().startswith("ЗАДАЧА ")]


def _classify_archetype(category: str, solution: str) -> str:
    category_lower = category.lower()
    solution_lower = solution.lower()

    if "фильтрац" in category_lower or "_utils.array.new()" in solution:
        return "filtering"
    if "нормализац" in category_lower or ("type(" in solution and "return {" in solution):
        return "normalization"
    if any(token in category_lower for token in ("дата", "время")) and any(
        token in solution_lower
        for token in ("string.format", "string.sub", "os.time", "parse_iso8601", "days_since_epoch", "epoch")
    ):
        return "datetime_conversion"
    if category_lower == "извлечение данных" or (
        "return wf." in solution and "_utils.array.new()" not in solution and _ARITHMETIC_PATTERN.search(solution) is None
    ):
        return "simple_extraction"
    return "transformation"


def _derive_risk_tags(solution: str) -> list[str]:
    tags: list[str] = []

    if "_utils.array.new()" in solution:
        tags.append("array_allocation")
    if "nil" in solution:
        tags.append("nil_handling")
    if '""' in solution:
        tags.append("empty_value_filtering")
    if "key ~=" in solution:
        tags.append("field_whitelist")
    if "type(" in solution and "return {" in solution:
        tags.extend(["array_semantics", "type_normalization"])
    if "string.format" in solution and ".000000Z" in solution:
        tags.append("datetime_format")
    if "string.sub" in solution:
        tags.append("substring_bounds")
    if any(token in solution for token in ("os.time", "parse_iso8601", "days_since_epoch", "epoch")):
        tags.append("datetime_conversion")
    if any(token in solution for token in ("offset_sign", "offset_hour", "offset_min")):
        tags.append("timezone_offset")
    if "wf.initVariables." in solution:
        tags.append("init_variables")
    if _ARITHMETIC_PATTERN.search(solution) or "tonumber(" in solution:
        tags.append("numeric_transform")
    if " = nil" in solution or "]= nil" in solution:
        tags.append("table_mutation")
    if _INDEXING_PATTERN.search(solution):
        tags.append("array_indexing")
        tags.append("empty_array")

    return list(dict.fromkeys(tags))


def _extract_input_roots(solution: str) -> list[str]:
    roots = _ROOT_PATTERN.findall(solution)
    return list(dict.fromkeys(roots))


def _normalize_title(prompt: str, number: str) -> str:
    single_line_prompt = _WHITESPACE_NORMALIZER.sub(" ", prompt).strip()
    if len(single_line_prompt) <= 100:
        return single_line_prompt
    return f"Lua task {number}"


def _extract_section(block: str, start_marker: str, end_marker: str | None) -> str:
    start_index = block.index(start_marker) + len(start_marker)
    if end_marker is None:
        return block[start_index:].strip()
    end_index = block.index(end_marker, start_index)
    return block[start_index:end_index].strip()


def _load_context(context_text: str) -> dict[str, object]:
    try:
        return json.loads(context_text)
    except json.JSONDecodeError:
        repaired_text = _repair_json_like_context(context_text)
        return json.loads(repaired_text)


def _repair_json_like_context(context_text: str) -> str:
    characters = list(context_text)
    stack: list[str] = []
    in_string = False
    escaped = False

    for index, character in enumerate(characters):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue

        if character == '"':
            in_string = True
            continue

        if character == "{":
            stack.append("}")
            continue
        if character == "[":
            stack.append("]")
            continue
        if character not in {"]", "}"}:
            continue
        if not stack:
            continue
        expected_closer = stack.pop()
        if character != expected_closer:
            characters[index] = expected_closer

    while stack:
        characters.append(stack.pop())

    return "".join(characters)


def _parse_case(block: str, *, id_prefix: str, source_id: str) -> dict[str, object]:
    match = _HEADER_PATTERN.match(block)
    if match is None:
        raise ValueError(f"Failed to parse benchmark header:\n{block[:400]}")

    number = match.group("number")
    category = match.group("category").strip()
    prompt = _extract_section(block, "Запрос пользователя:\n", "\n\nКонтекст:\n")
    context = _load_context(_extract_section(block, "Контекст:\n", "\n\nЧто нужно вернуть:\n"))
    return_description = _extract_section(block, "Что нужно вернуть:\n", "\n\nОжидаемое решение (Lua):\n")
    solution = _extract_section(block, "Ожидаемое решение (Lua):\n", None)
    archetype = _classify_archetype(category, solution)

    return {
        "id": f"{id_prefix}{number}",
        "title": _normalize_title(prompt, number),
        "category": category,
        "prompt": prompt,
        "context": context,
        "archetype": archetype,
        "allowed_output_modes": ["raw_lua"],
        "primary_output_mode": "raw_lua",
        "expected_outputs": {"raw_lua": solution},
        "input_roots": _extract_input_roots(solution),
        "risk_tags": _derive_risk_tags(solution),
        "expected_return_description": return_description,
        "source_ref": f"{source_id}:{number}",
    }


def main() -> None:
    parser = _parse_args()
    args = parser.parse_args()

    source_path = Path(args.source)
    output_path = Path(args.output)

    text = source_path.read_text(encoding="utf-8", errors="replace")
    blocks = _split_task_blocks(text)
    cases = [_parse_case(block, id_prefix=args.id_prefix, source_id=args.source_id) for block in blocks]

    if len(cases) != args.expected_count:
        raise ValueError(f"Expected {args.expected_count} tasks, parsed {len(cases)}")

    payload = {
        "dataset_id": args.dataset_id,
        "version": "1.0.0",
        "retrieval_eligible": False,
        "source_refs": [
            {
                "id": args.source_id,
                "type": "txt",
                "location": str(source_path),
                "notes": args.source_note,
            }
        ],
        "cases": cases,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    archetype_counts = Counter(case["archetype"] for case in cases)
    print(f"Wrote {len(cases)} cases to {output_path}")
    for archetype, count in sorted(archetype_counts.items()):
        print(f"{archetype}: {count}")


if __name__ == "__main__":
    main()
