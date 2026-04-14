from packages.orchestrator.critic import build_critic_report, build_semantic_critic_prompt
from packages.shared.quality import ValidationFinding, ValidatorReport


def _pass_report(name: str) -> ValidatorReport:
    return ValidatorReport(validator=name, status="pass")


def test_build_critic_report_localizes_ambiguous_clarification_to_russian() -> None:
    path_report = ValidatorReport(
        validator="path_validator",
        status="fail",
        findings=(
            ValidationFinding(
                validator="path_validator",
                failure_class="mixed_root_families",
                message="Candidate mixes wf.vars.* and wf.initVariables.* without a safe basis.",
                location="response",
                repairable=False,
                ambiguous=True,
                suggestion="Which data root should be used: wf.vars.* or wf.initVariables.*?",
            ),
        ),
    )

    report = build_critic_report(
        _pass_report("format_validator"),
        _pass_report("syntax_validator"),
        _pass_report("static_validator"),
        path_report,
        _pass_report("runtime_validator"),
        _pass_report("semantic_validator"),
        output_mode="raw_lua",
        repair_count=0,
        clarification_count=0,
        repeated_failure_class=False,
        oscillation_detected=False,
        language="ru",
    )

    assert report == {
        "action": "clarification",
        "failure_class": "mixed_root_families",
        "message": "Кандидат смешивает wf.vars.* и wf.initVariables.* без безопасного основания.",
        "clarification_question": "Какой источник данных нужно использовать: wf.vars.* или wf.initVariables.*?",
    }


def test_build_semantic_critic_prompt_requests_selected_language() -> None:
    prompt = build_semantic_critic_prompt(
        prompt="Task prompt",
        candidate="return wf.vars.value",
        output_mode="raw_lua",
        format_report=_pass_report("format_validator"),
        syntax_report=_pass_report("syntax_validator"),
        static_report=_pass_report("static_validator"),
        principle_report=_pass_report("principle_validator"),
        runtime_report=_pass_report("runtime_validator"),
        language="ru",
    )

    assert "Write the JSON message and suggestion fields in Russian." in prompt
