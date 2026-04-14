import json
from pathlib import Path

import pytest

from packages.benchmark.principles import evaluate_case_by_principles


def test_principle_evaluator_accepts_semantically_equivalent_transformation() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    public_cases = json.loads((repo_root / "benchmark" / "public_cases.json").read_text(encoding="utf-8"))["cases"]
    case = next(item for item in public_cases if item["id"] == "case-04-iso8601-format")

    candidate = "\n".join(
        [
            "local DATUM = wf.vars.json.IDOC.ZCDF_HEAD.DATUM",
            "local TIME = wf.vars.json.IDOC.ZCDF_HEAD.TIME",
            "local function safe_sub(str, start, finish)",
            "  if type(str) ~= \"string\" then return \"00\" end",
            "  local s = string.sub(str, start, math.min(finish, #str))",
            "  return s ~= \"\" and s or \"00\"",
            "end",
            "local year = safe_sub(DATUM, 1, 4)",
            "local month = safe_sub(DATUM, 5, 6)",
            "local day = safe_sub(DATUM, 7, 8)",
            "local hour = safe_sub(TIME, 1, 2)",
            "local minute = safe_sub(TIME, 3, 4)",
            "local second = safe_sub(TIME, 5, 6)",
            "return string.format('%s-%s-%sT%s:%s:%s.000000Z', year, month, day, hour, minute, second)",
        ]
    )

    report = evaluate_case_by_principles(case, candidate)

    assert report["status"] == "pass"
    assert report["summary"]["passed"] >= report["summary"]["required"]


def test_principle_evaluator_rejects_wrong_patch_mode_shape() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    public_cases = json.loads((repo_root / "benchmark" / "public_cases.json").read_text(encoding="utf-8"))["cases"]
    case = next(item for item in public_cases if item["id"] == "case-07-add-squared-variable")

    candidate = '{"wf.vars.squared":"lua{return (wf.vars.number or 0) * (wf.vars.number or 0)}lua"}'
    report = evaluate_case_by_principles(case, candidate)

    assert report["status"] == "fail"
    assert any(check["name"] == "patch_expected_keys_present" and check["status"] == "fail" for check in report["checks"])


def test_principle_evaluator_accepts_direct_named_field_clearing_for_whitelist_like_task() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    public_cases = json.loads((repo_root / "benchmark" / "public_cases.json").read_text(encoding="utf-8"))["cases"]
    case = next(item for item in public_cases if item["id"] == "case-03-restbody-cleanup")

    candidate = "\n".join(
        [
            "local result = wf.vars.RESTbody.result",
            "for _, filteredEntry in pairs(result) do",
            '  filteredEntry["ID"] = nil',
            '  filteredEntry["ENTITY_ID"] = nil',
            '  filteredEntry["CALL"] = nil',
            "end",
            "",
            "return result",
        ]
    )

    report = evaluate_case_by_principles(case, candidate)

    assert report["status"] == "pass"
    assert any(
        check["name"] == "field_value_clearing" and check["status"] == "pass"
        for check in report["checks"]
    )


def test_principle_evaluator_accepts_mixed_case_hyphen_field_whitelist() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    synthetic_cases = json.loads((repo_root / "benchmark" / "synthetic_cases.json").read_text(encoding="utf-8"))["cases"]
    case = next(item for item in synthetic_cases if item["id"] == "synthetic-case-07-cleanup-headers")

    candidate = "\n".join(
        [
            "local headers = wf.vars.headers",
            "for key, _ in pairs(headers) do",
            '  if key ~= "Authorization" and key ~= "Content-Type" then',
            "    headers[key] = nil",
            "  end",
            "end",
            "",
            "return headers",
        ]
    )

    report = evaluate_case_by_principles(case, candidate)

    assert report["status"] == "pass"
    assert any(
        check["name"] == "field_whitelist_preservation" and check["status"] == "pass"
        for check in report["checks"]
    )


def test_principle_evaluator_accepts_in_place_field_enrichment_as_array_result() -> None:
    case = {
        "prompt": (
            "Для каждого файла в files добавь boolean-поле isImage: true, если extension равно png или jpg, "
            "иначе false. Можно обновлять исходные объекты."
        ),
        "context": {"wf": {"vars": {"files": [{"extension": "png"}, {"extension": "pdf"}]}}},
        "archetype": "filtering",
        "primary_output_mode": "raw_lua",
        "input_roots": ["wf.vars.files"],
        "risk_tags": ["array_allocation"],
        "expected_outputs": {"raw_lua": "return wf.vars.files"},
    }
    candidate = "\n".join(
        [
            "for _, file in ipairs(wf.vars.files) do",
            '  file.isImage = (file.extension == "png" or file.extension == "jpg")',
            "end",
            "return wf.vars.files",
        ]
    )

    report = evaluate_case_by_principles(case, candidate)

    assert report["status"] == "pass"
    assert any(
        check["name"] == "array_result_allocation" and check["status"] == "pass"
        for check in report["checks"]
    )


def test_principle_evaluator_accepts_alias_conditional_field_enrichment_as_array_result() -> None:
    case = {
        "prompt": (
            "Для каждого файла в files добавь boolean-поле isImage: true, если extension равно png или jpg, "
            "иначе false. Можно обновлять исходные объекты."
        ),
        "context": {"wf": {"vars": {"files": [{"extension": "png"}, {"extension": "pdf"}]}}},
        "archetype": "filtering",
        "primary_output_mode": "raw_lua",
        "input_roots": ["wf.vars.files"],
        "risk_tags": ["array_allocation"],
        "expected_outputs": {"raw_lua": "return wf.vars.files"},
    }
    candidate = "\n".join(
        [
            "local files = wf.vars.files",
            "for _, file in ipairs(files) do",
            '  if file.extension == "png" or file.extension == "jpg" then',
            "    file.isImage = true",
            "  else",
            "    file.isImage = false",
            "  end",
            "end",
            "return files",
        ]
    )

    report = evaluate_case_by_principles(case, candidate)

    assert report["status"] == "pass"
    assert any(
        check["name"] == "array_result_allocation" and check["status"] == "pass"
        for check in report["checks"]
    )


def test_principle_evaluator_rejects_filter_without_array_result_contract() -> None:
    case = {
        "prompt": "Оставь только те строки, где заполнены sku.",
        "context": {"wf": {"vars": {"lines": [{"sku": "X1"}, {"sku": ""}]}}},
        "archetype": "filtering",
        "primary_output_mode": "raw_lua",
        "input_roots": ["wf.vars.lines"],
        "risk_tags": ["array_allocation"],
        "expected_outputs": {"raw_lua": "return filtered lines"},
    }
    candidate = "\n".join(
        [
            "for _, line in ipairs(wf.vars.lines) do",
            '  if line.sku == "" then',
            "    line.skip = true",
            "  end",
            "end",
            "return wf.vars.lines",
        ]
    )

    report = evaluate_case_by_principles(case, candidate)

    assert report["status"] == "fail"
    assert any(
        check["name"] == "array_result_allocation" and check["status"] == "fail"
        for check in report["checks"]
    )


def test_principle_evaluator_accepts_per_item_type_normalization_returning_parent_array() -> None:
    case = {
        "prompt": "Сделай так, чтобы attachments у каждого сообщения всегда были массивом. Верни массив messages.",
        "context": {"wf": {"vars": {"messages": [{"attachments": {"name": "a.txt"}}]}}},
        "archetype": "normalization",
        "primary_output_mode": "raw_lua",
        "input_roots": ["wf.vars.messages"],
        "risk_tags": ["array_semantics", "type_normalization"],
        "expected_outputs": {
            "raw_lua": "\n".join(
                [
                    "local messages = wf.vars.messages",
                    "for _, message in ipairs(messages) do",
                    "  message.attachments = ensureArray(message.attachments)",
                    "end",
                    "return messages",
                ]
            )
        },
    }
    candidate = "\n".join(
        [
            "local messages = wf.vars.messages",
            "for _, message in ipairs(messages) do",
            "  local attachments = message.attachments",
            "  if attachments == nil then",
            "    message.attachments = {}",
            '  elseif type(attachments) ~= "table" then',
            "    message.attachments = {attachments}",
            "  else",
            "    local isArray = true",
            "    for k, _ in pairs(attachments) do",
            '      if type(k) ~= "number" or math.floor(k) ~= k then',
            "        isArray = false",
            "        break",
            "      end",
            "    end",
            "    if not isArray then",
            "      message.attachments = {attachments}",
            "    end",
            "  end",
            "end",
            "return messages",
        ]
    )

    report = evaluate_case_by_principles(case, candidate)

    assert report["status"] == "pass"
    assert any(
        check["name"] == "type_normalization_guard" and check["status"] == "pass"
        for check in report["checks"]
    )
    assert any(
        check["name"] == "type_normalization_return_contract" and check["status"] == "pass"
        for check in report["checks"]
    )


def test_principle_evaluator_accepts_field_type_normalization_returning_field_array() -> None:
    case = {
        "prompt": "Сделай так, чтобы поле roles в user всегда было массивом строк. Верни нормализованный массив roles.",
        "context": {"wf": {"vars": {"user": {"roles": "admin"}}}},
        "archetype": "normalization",
        "primary_output_mode": "raw_lua",
        "input_roots": ["wf.vars.user.roles"],
        "risk_tags": ["array_semantics", "type_normalization"],
        "expected_outputs": {"raw_lua": "local roles = wf.vars.user.roles\nreturn roles"},
    }
    candidate = "\n".join(
        [
            "local user = wf.vars.user",
            "local roles = user.roles",
            "if roles == nil then",
            "  user.roles = {}",
            'elseif type(roles) ~= "table" then',
            "  user.roles = {roles}",
            "else",
            "  for k, _ in pairs(roles) do",
            '    if type(k) ~= "number" or math.floor(k) ~= k then',
            "      user.roles = {roles}",
            "      break",
            "    end",
            "  end",
            "end",
            "return user.roles",
        ]
    )

    report = evaluate_case_by_principles(case, candidate)

    assert report["status"] == "pass"


def test_principle_evaluator_accepts_scalar_type_normalization() -> None:
    case = {
        "prompt": "Если phone хранится строкой, преобразуй его в массив из одного элемента.",
        "context": {"wf": {"vars": {"phone": "79991234567"}}},
        "archetype": "normalization",
        "primary_output_mode": "raw_lua",
        "input_roots": ["wf.vars.phone"],
        "risk_tags": ["array_semantics", "type_normalization"],
        "expected_outputs": {"raw_lua": "local phone = wf.vars.phone\nif type(phone) == \"string\" then\n  return {phone}\nend\nreturn phone"},
    }
    candidate = "\n".join(
        [
            "local phone = wf.vars.phone",
            'if type(phone) == "string" then',
            "  return {phone}",
            "end",
            "return phone",
        ]
    )

    report = evaluate_case_by_principles(case, candidate)

    assert report["status"] == "pass"


def test_principle_evaluator_rejects_field_type_normalization_without_field_array_return() -> None:
    case = {
        "prompt": "Сделай так, чтобы field tags в article всегда был массивом. Верни нормализованный массив tags.",
        "context": {"wf": {"vars": {"article": {"tags": {"value": "news"}}}}},
        "archetype": "normalization",
        "primary_output_mode": "raw_lua",
        "input_roots": ["wf.vars.article.tags"],
        "risk_tags": ["array_semantics", "type_normalization"],
        "expected_outputs": {"raw_lua": "local tags = wf.vars.article.tags\nreturn tags"},
    }
    candidate = "\n".join(
        [
            "local article = wf.vars.article",
            "local tags = article.tags",
            "if tags == nil then",
            "  article.tags = {}",
            "  return",
            "end",
            'if type(tags) ~= "table" then',
            "  article.tags = {tags}",
            "  return",
            "end",
            "for k, _ in pairs(tags) do",
            '  if type(k) ~= "number" or math.floor(k) ~= k then',
            "    article.tags = {tags}",
            "    break",
            "  end",
            "end",
        ]
    )

    report = evaluate_case_by_principles(case, candidate)

    assert report["status"] == "fail"
    assert any(
        check["name"] == "type_normalization_guard" and check["status"] == "pass"
        for check in report["checks"]
    )
    assert any(
        check["name"] == "type_normalization_return_contract" and check["status"] == "fail"
        for check in report["checks"]
    )


@pytest.mark.parametrize(
    "candidate",
    [
        "return wf.vars.availableQty - wf.vars.reservedQty",
        "return wf.vars.durationMs / 1000",
        "return math.floor(wf.vars.durationSec / 60)",
        "\n".join(
            [
                "local sum = 0",
                "for _, item in ipairs(wf.vars.items) do",
                "  sum = sum + item.quantity",
                "end",
                "return sum",
            ]
        ),
        "\n".join(
            [
                "local sum = 0",
                "for _, order in ipairs(wf.vars.orders) do",
                "  sum = sum + (order.discountAmount or 0)",
                "end",
                "return sum",
            ]
        ),
        "\n".join(
            [
                "local result = {}",
                "for _, order in ipairs(wf.vars.orders) do",
                "  local status = order.status",
                "  result[status] = (result[status] or 0) + 1",
                "end",
                "return result",
            ]
        ),
        "\n".join(
            [
                "local totalLength = 0",
                "for _, tag in ipairs(wf.vars.tags) do",
                "  totalLength = totalLength + #tag",
                "end",
                "return totalLength",
            ]
        ),
    ],
)
def test_principle_evaluator_accepts_common_numeric_operations(candidate: str) -> None:
    case = {
        "prompt": "Do a numeric transformation.",
        "context": {"wf": {"vars": {"availableQty": 5, "reservedQty": 2}}},
        "archetype": "transformation",
        "primary_output_mode": "raw_lua",
        "input_roots": [
            "wf.vars.availableQty",
            "wf.vars.reservedQty",
            "wf.vars.durationMs",
            "wf.vars.durationSec",
            "wf.vars.items",
            "wf.vars.orders",
            "wf.vars.tags",
        ],
        "risk_tags": ["numeric_transform"],
        "expected_outputs": {"raw_lua": candidate},
    }

    report = evaluate_case_by_principles(case, candidate)

    assert report["status"] == "pass"
    assert any(
        check["name"] == "numeric_operation_present" and check["status"] == "pass"
        for check in report["checks"]
    )


def test_synthetic_benchmark_pack_exists_and_has_cases() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    synthetic_path = repo_root / "benchmark" / "synthetic_cases.json"
    synthetic_cases = json.loads(synthetic_path.read_text(encoding="utf-8"))["cases"]

    assert len(synthetic_cases) >= 20
