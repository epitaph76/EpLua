import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from adapters.model import OllamaModelAdapter
from runtime_policy import RELEASE_MODE, RuntimeOptions, normalize_mode

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from packages.orchestrator.agent_prompt import AgentPrompt  # noqa: E402
from packages.orchestrator.planner import (  # noqa: E402
    PlannerResult,
    apply_planner_agent_response,
    build_lowcode_clarifier_agent_prompt,
    build_lowcode_planner_agent_prompt,
    plan_task,
    parse_clarifier_agent_response,
)
from packages.orchestrator.critic import _localize_text as _localize_critic_text  # noqa: E402
from packages.orchestrator.critic import (  # noqa: E402
    build_critic_report,
    build_semantic_critic_agent_prompt,
    parse_semantic_critic_response,
)
from packages.orchestrator.prompter import (  # noqa: E402
    LOWCODE_LUA_EXPECTED_RESULT_FORMAT,
    PromptBuilderResult,
    apply_assisted_repair_summarizer_agent_response,
    apply_lowcode_prompter_agent_response,
    build_assisted_repair_summarizer_agent_prompt,
    build_lowcode_prompt_builder_result,
    build_lowcode_prompter_agent_prompt,
    build_lowcode_repair_prompt_builder_result,
    render_lowcode_generator_prompt,
)
from packages.shared.language import DEFAULT_LANGUAGE, normalize_language  # noqa: E402
from packages.shared.quality import ValidationBundle, ValidatorReport  # noqa: E402
from packages.validators.core import LOWCODE_JSON, run_validation_pipeline  # noqa: E402

_DEFAULT_ARCHETYPE = "transformation"
_DEFAULT_OUTPUT_MODE = LOWCODE_JSON
_DEFAULT_REPAIR_BUDGET = 2
_MAX_GENERATOR_CONTINUATIONS = 4


@dataclass(frozen=True)
class _PromptGeneration:
    response: str
    truncation_guard: dict[str, object] | None = None
    temporary_paths: tuple[Path, ...] = ()


class GenerationService:
    def __init__(self, model_adapter: OllamaModelAdapter | None = None) -> None:
        self._model_adapter = model_adapter or OllamaModelAdapter()

    def plan(
        self,
        task_text: str,
        provided_context: str | None = None,
        *,
        archetype: str | None = None,
        output_mode: str | None = None,
        input_roots: list[str] | None = None,
        risk_tags: list[str] | None = None,
        debug: bool = False,
        mode: str = RELEASE_MODE,
        model: str | None = None,
        runtime_options: dict[str, int | float] | RuntimeOptions | None = None,
        allow_cloud_model: bool = False,
        language: str = DEFAULT_LANGUAGE,
        clarifications: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        model_adapter = self._adapter_for_request(
            mode=mode,
            model=model,
            runtime_options=runtime_options,
            allow_cloud_model=allow_cloud_model,
        )
        clarifier_questions, clarifier_layer_call, clarifier_debug_result = self._run_clarifier_agent(
            model_adapter=model_adapter,
            task_text=task_text,
            provided_context=provided_context,
            archetype=archetype,
            output_mode=output_mode,
            input_roots=input_roots,
            risk_tags=risk_tags,
            language=language,
            clarifications=clarifications,
        )
        planner_result, agent_layer_calls = self._plan_lowcode_task(
            model_adapter=model_adapter,
            task_text=task_text,
            provided_context=provided_context,
            archetype=archetype,
            output_mode=output_mode,
            input_roots=input_roots,
            risk_tags=risk_tags,
            language=language,
            clarifications=clarifications,
        )
        if clarifier_layer_call is not None:
            agent_layer_calls.insert(0, clarifier_layer_call)
        if clarifier_questions:
            planner_result = self._planner_result_with_clarifier_questions(
                planner_result=planner_result,
                questions=clarifier_questions,
            )
        debug_payload = None
        if debug:
            debug_payload = {
                "planner_result": planner_result.to_debug_dict(),
                "agent_layer_calls": agent_layer_calls,
            }
            if clarifier_debug_result is not None:
                debug_payload["clarifier_result"] = clarifier_debug_result
        return {
            "task_spec": planner_result.task_spec.to_dict(),
            "clarification_required": planner_result.clarification_required,
            "questions": [self._clarification_question_payload(question) for question in planner_result.task_spec.clarification_questions],
            "trace": [
                "request_received",
                *[str(call["phase"]) for call in agent_layer_calls if call.get("phase") in {"clarifier", "planner"}],
                "response_ready",
            ],
            "debug": debug_payload,
        }

    def generate(
        self,
        task_text: str,
        provided_context: str | None = None,
        *,
        archetype: str | None = None,
        output_mode: str | None = None,
        input_roots: list[str] | None = None,
        risk_tags: list[str] | None = None,
        debug: bool = False,
        mode: str = RELEASE_MODE,
        model: str | None = None,
        runtime_options: dict[str, int | float] | RuntimeOptions | None = None,
        allow_cloud_model: bool = False,
        language: str = DEFAULT_LANGUAGE,
        clarifications: list[dict[str, object]] | None = None,
        feedback_text: str | None = None,
        previous_candidate: str | None = None,
        assisted_repair_option_id: str | None = None,
        repair_budget: int = _DEFAULT_REPAIR_BUDGET,
        progress_callback: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        self._emit_progress(progress_callback, "request_received")
        temporary_paths: list[Path] = []
        effective_task_text = task_text
        if isinstance(feedback_text, str) and feedback_text.strip():
            effective_task_text = self._feedback_task_text(
                task_text=task_text,
                feedback_text=feedback_text,
                previous_candidate=previous_candidate,
                language=language,
            )
        model_adapter = self._adapter_for_request(
            mode=mode,
            model=model,
            runtime_options=runtime_options,
            allow_cloud_model=allow_cloud_model,
        )
        repair_budget = max(1, repair_budget)
        prompt_builder_result, planner_result, agent_layer_calls = self._build_lowcode_prompt_builder_result(
            model_adapter=model_adapter,
            task_text=effective_task_text,
            provided_context=provided_context,
            archetype=archetype,
            output_mode=output_mode,
            input_roots=input_roots,
            risk_tags=risk_tags,
            language=language,
            clarifications=clarifications,
            progress_callback=progress_callback,
        )
        prompt = render_lowcode_generator_prompt(prompt_builder_result.agent_prompt)
        generation_result = self._generate_from_prompt_with_continuation_guard(
            model_adapter=model_adapter,
            prompt=prompt,
            phase="generation",
            temporary_paths=temporary_paths,
        )
        code = generation_result.response
        self._emit_progress(progress_callback, "generation")
        model_calls = [
            self._model_call(
                phase="generation",
                agent="generator",
                prompt=prompt,
                raw_response=code,
                truncation_guard=generation_result.truncation_guard,
            )
        ]
        trace = [
            "request_received",
            *(
                ["assisted_repair_received"]
                if isinstance(assisted_repair_option_id, str) and assisted_repair_option_id.strip()
                else []
            ),
            *(
                ["feedback_received"]
                if (
                    isinstance(feedback_text, str)
                    and feedback_text.strip()
                    and not (isinstance(assisted_repair_option_id, str) and assisted_repair_option_id.strip())
                )
                else []
            ),
            *[str(call["phase"]) for call in agent_layer_calls if call.get("phase") in {"planner", "prompter"}],
            "generation",
        ]
        validation_result = self._run_deterministic_validation(
            candidate=code,
            prompt=prompt,
            prompt_builder_result=prompt_builder_result,
            planner_result=planner_result,
            model_adapter=model_adapter,
            model_calls=model_calls,
            repair_count=0,
            phase="deterministic_validation",
        )
        validation_passes = list(validation_result["validation_passes"])
        trace.append("deterministic_validation")
        self._emit_progress(progress_callback, "deterministic_validation")
        if validation_result.get("semantic_validation_ran"):
            trace.append("semantic_validation")
            self._emit_progress(progress_callback, "semantic_validation")
        repair_count = 0
        generation_pass_count = 1

        while (
            validation_result["validation_status"] != "passed"
            and validation_result["critic_report"].get("action") == "repair"
            and generation_pass_count < repair_budget
        ):
            repair_count += 1
            repair_prompt_builder_result = self._build_repair_prompt_builder_result(
                model_adapter=model_adapter,
                planner_result=planner_result,
                original_prompt_builder_result=prompt_builder_result,
                current_candidate=code,
                repair_instruction=str(validation_result["critic_report"].get("repair_prompt") or ""),
                validation_pass=validation_passes[-1],
                repair_count=repair_count,
                agent_layer_calls=agent_layer_calls,
                progress_callback=progress_callback,
            )
            repair_prompt = render_lowcode_generator_prompt(repair_prompt_builder_result.agent_prompt)
            trace.append("repair_generation")
            generation_result = self._generate_from_prompt_with_continuation_guard(
                model_adapter=model_adapter,
                prompt=repair_prompt,
                phase="repair_generation",
                temporary_paths=temporary_paths,
            )
            code = generation_result.response
            self._emit_progress(progress_callback, "repair_generation")
            generation_pass_count += 1
            model_calls.append(
                self._model_call(
                    phase="repair_generation",
                    agent="generator",
                    prompt=repair_prompt,
                    raw_response=code,
                    truncation_guard=generation_result.truncation_guard,
                )
            )
            validation_result = self._run_deterministic_validation(
                candidate=code,
                prompt=prompt,
                prompt_builder_result=repair_prompt_builder_result,
                planner_result=planner_result,
                model_adapter=model_adapter,
                model_calls=model_calls,
                repair_count=repair_count,
                phase="deterministic_validation",
            )
            validation_passes.extend(validation_result["validation_passes"])
            trace.append("deterministic_validation")
            self._emit_progress(progress_callback, "deterministic_validation")
            if validation_result.get("semantic_validation_ran"):
                trace.append("semantic_validation")
                self._emit_progress(progress_callback, "semantic_validation")

        final_status = str(validation_result["validation_status"])
        stop_reason = str(validation_result["stop_reason"])
        assisted_repair_request = None
        if final_status == "passed" and repair_count:
            final_status = "repaired"
        elif final_status != "passed" and generation_pass_count >= repair_budget:
            stop_reason = "repair_exhausted"
            if not (isinstance(assisted_repair_option_id, str) and assisted_repair_option_id.strip()):
                previous_agent_call_count = len(agent_layer_calls)
                assisted_repair_request = self._build_assisted_repair_request(
                    task_text=task_text,
                    planner_result=planner_result,
                    latest_candidate=code,
                    validation_pass=validation_passes[-1],
                    validation_history=tuple(validation_passes),
                    critic_report=validation_result["critic_report"],
                    language=language,
                    model_adapter=model_adapter,
                    agent_layer_calls=agent_layer_calls,
                )
                if len(agent_layer_calls) > previous_agent_call_count:
                    trace.append("assisted_repair_summarizer")
        trace.append("response_ready")
        self._emit_progress(progress_callback, "response_ready")
        response_payload = {
            "code": code,
            "validation_status": final_status,
            "stop_reason": stop_reason,
            "trace": trace,
            "validator_report": self._validator_report(validation_passes, final_status),
            "critic_report": validation_result["critic_report"],
            "repair_count": repair_count,
            "clarification_count": 0,
            "assisted_repair_request": assisted_repair_request,
            "output_mode": planner_result.task_spec.output_mode,
            "archetype": planner_result.task_spec.archetype,
            "debug": self._build_debug_payload(
                prompt_builder_result,
                prompt,
                code,
                agent_layer_calls,
                planner_result,
                validation_passes,
                model_calls,
            )
            if debug
            else None,
        }
        self._cleanup_temporary_paths(temporary_paths)
        return response_payload

    def _run_clarifier_agent(
        self,
        *,
        model_adapter: OllamaModelAdapter,
        task_text: str,
        provided_context: str | None,
        archetype: str | None,
        output_mode: str | None,
        input_roots: list[str] | None,
        risk_tags: list[str] | None,
        language: str,
        clarifications: list[dict[str, object]] | None,
    ) -> tuple[tuple[dict[str, object], ...], dict[str, object] | None, dict[str, object] | None]:
        if self._clarifications_tuple(clarifications):
            return tuple(), None, None

        agent_runner = getattr(model_adapter, "generate_from_agent", None)
        if not callable(agent_runner):
            return tuple(), None, None

        fallback_result = plan_task(
            task_text,
            provided_context,
            language=language,
            archetype=archetype or _DEFAULT_ARCHETYPE,
            output_mode=output_mode or _DEFAULT_OUTPUT_MODE,
            input_roots=input_roots,
            risk_tags=tuple(risk_tags or ()),
            explicit_archetype=archetype is not None,
            explicit_output_mode=output_mode is not None,
        )
        clarifier_agent_prompt = build_lowcode_clarifier_agent_prompt(
            task_text=task_text,
            provided_context=provided_context,
            fallback_result=fallback_result,
        )
        clarifier_raw_response = agent_runner(clarifier_agent_prompt)
        questions = parse_clarifier_agent_response(clarifier_raw_response)
        debug_result: dict[str, object] = {
            "agent": "clarifier",
            "source": "agent" if questions else "agent_no_questions",
            "clarification_required": bool(questions),
            "questions": [self._clarification_question_payload(question) for question in questions],
        }
        layer_call = self._agent_layer_call(
            phase="clarifier",
            agent_prompt=clarifier_agent_prompt,
            raw_response=clarifier_raw_response,
            result_key="clarifier_result",
            result=debug_result,
        )
        return questions, layer_call, debug_result

    def _planner_result_with_clarifier_questions(
        self,
        *,
        planner_result: PlannerResult,
        questions: tuple[dict[str, object], ...],
    ) -> PlannerResult:
        first_question = str(questions[0].get("question") or "").strip() if questions else None
        task_spec = replace(
            planner_result.task_spec,
            output_mode="clarification",
            expected_shape="clarification_question",
            clarification_required=True,
            clarification_question=first_question,
            clarification_questions=questions,
        )
        return replace(
            planner_result,
            task_spec=task_spec,
            clarification_required=True,
        )

    def _build_lowcode_prompt_builder_result(
        self,
        *,
        model_adapter: OllamaModelAdapter,
        task_text: str,
        provided_context: str | None,
        archetype: str | None,
        output_mode: str | None,
        input_roots: list[str] | None,
        risk_tags: list[str] | None,
        language: str,
        clarifications: list[dict[str, object]] | None,
        progress_callback: Callable[[str], None] | None,
    ) -> tuple[PromptBuilderResult, PlannerResult, list[dict[str, object]]]:
        agent_runner = getattr(model_adapter, "generate_from_agent", None)
        planner_result, agent_layer_calls = self._plan_lowcode_task(
            model_adapter=model_adapter,
            task_text=task_text,
            provided_context=provided_context,
            archetype=archetype,
            output_mode=output_mode,
            input_roots=input_roots,
            risk_tags=risk_tags,
            language=language,
            clarifications=clarifications,
        )
        self._emit_progress(progress_callback, "planner")
        prompt_builder_result = build_lowcode_prompt_builder_result(
            task_text=task_text,
            provided_context=provided_context,
            planner_result=planner_result,
            clarifications=self._clarifications_tuple(clarifications),
        )
        if callable(agent_runner):
            prompter_agent_prompt = build_lowcode_prompter_agent_prompt(
                task_text=task_text,
                provided_context=provided_context,
                planner_result=planner_result,
                fallback_result=prompt_builder_result,
                clarifications=self._clarifications_tuple(clarifications),
            )
            prompter_raw_response = agent_runner(prompter_agent_prompt)
            prompt_builder_result = apply_lowcode_prompter_agent_response(prompter_raw_response, prompt_builder_result)
            agent_layer_calls.append(
                self._agent_layer_call(
                    phase="prompter",
                    agent_prompt=prompter_agent_prompt,
                    raw_response=prompter_raw_response,
                    result_key="prompt_builder_result",
                    result=prompt_builder_result.to_debug_dict(),
                )
            )
            self._emit_progress(progress_callback, "prompter")
        return prompt_builder_result, planner_result, agent_layer_calls

    def _plan_lowcode_task(
        self,
        *,
        model_adapter: OllamaModelAdapter,
        task_text: str,
        provided_context: str | None,
        archetype: str | None,
        output_mode: str | None,
        input_roots: list[str] | None,
        risk_tags: list[str] | None,
        language: str,
        clarifications: list[dict[str, object]] | None,
    ) -> tuple[PlannerResult, list[dict[str, object]]]:
        agent_runner = getattr(model_adapter, "generate_from_agent", None)
        risk_tags_tuple = tuple(risk_tags or ())
        clarification_tuple = self._clarifications_tuple(clarifications)
        planner_result = plan_task(
            task_text,
            provided_context,
            language=language,
            archetype=archetype or _DEFAULT_ARCHETYPE,
            output_mode=output_mode or _DEFAULT_OUTPUT_MODE,
            input_roots=input_roots,
            risk_tags=risk_tags_tuple,
            explicit_archetype=archetype is not None,
            explicit_output_mode=output_mode is not None,
        )
        agent_layer_calls: list[dict[str, object]] = []
        if callable(agent_runner):
            planner_agent_prompt = build_lowcode_planner_agent_prompt(
                task_text=task_text,
                provided_context=provided_context,
                fallback_result=planner_result,
                clarifications=clarification_tuple,
            )
            planner_raw_response = agent_runner(planner_agent_prompt)
            planner_result = apply_planner_agent_response(planner_raw_response, planner_result)
            agent_layer_calls.append(
                self._agent_layer_call(
                    phase="planner",
                    agent_prompt=planner_agent_prompt,
                    raw_response=planner_raw_response,
                    result_key="planner_result",
                    result=planner_result.to_debug_dict(),
                )
            )
        return planner_result, agent_layer_calls

    def _clarifications_tuple(
        self,
        clarifications: list[dict[str, object]] | None,
    ) -> tuple[dict[str, object], ...]:
        if not clarifications:
            return tuple()
        normalized: list[dict[str, object]] = []
        for clarification in clarifications:
            if not isinstance(clarification, dict):
                continue
            question_id = str(clarification.get("question_id") or "").strip()
            option_id = str(clarification.get("option_id") or "").strip()
            free_text = clarification.get("free_text")
            if not question_id or not option_id:
                continue
            payload: dict[str, object] = {
                "question_id": question_id,
                "option_id": option_id,
                "free_text": free_text if isinstance(free_text, str) and free_text.strip() else None,
            }
            normalized.append(payload)
        return tuple(normalized)

    def _clarification_question_payload(self, question: dict[str, object]) -> dict[str, object]:
        options = question.get("options")
        return {
            "id": question.get("id"),
            "question": question.get("question"),
            "options": [dict(option) for option in options] if isinstance(options, tuple) else list(options or []),
            "default_option_id": question.get("default_option_id"),
        }

    def _feedback_task_text(
        self,
        *,
        task_text: str,
        feedback_text: str,
        previous_candidate: str | None,
        language: str,
    ) -> str:
        if language == "en":
            sections = [
                "Original task:",
                task_text,
                "",
                "Previous candidate:",
                previous_candidate or "",
                "",
                "User feedback:",
                feedback_text,
            ]
        else:
            sections = [
                "Исходная задача:",
                task_text,
                "",
                "Предыдущий кандидат:",
                previous_candidate or "",
                "",
                "Обратная связь пользователя:",
                feedback_text,
            ]
        return "\n".join(sections).strip()

    def _build_assisted_repair_request(
        self,
        *,
        task_text: str,
        planner_result: PlannerResult,
        latest_candidate: str,
        validation_pass: dict[str, object],
        validation_history: tuple[dict[str, object], ...],
        critic_report: dict[str, object],
        language: str,
        model_adapter: OllamaModelAdapter,
        agent_layer_calls: list[dict[str, object]],
    ) -> dict[str, object]:
        primary_finding = self._first_validation_finding(validation_pass)
        failure_classes = self._failure_classes_from_validation_pass(validation_pass)
        summary = self._assisted_repair_summary(primary_finding, critic_report, language)
        options = self._assisted_repair_options(primary_finding, critic_report, language)
        fallback_request = {
            "summary": summary,
            "failure_classes": failure_classes,
            "options": options,
            "latest_candidate": latest_candidate,
        }
        agent_runner = getattr(model_adapter, "generate_from_agent", None)
        if not callable(agent_runner):
            return fallback_request

        summarizer_prompt = build_assisted_repair_summarizer_agent_prompt(
            task_text=task_text,
            planner_result=planner_result,
            latest_candidate=latest_candidate,
            validation_pass=validation_pass,
            critic_report=critic_report,
            validation_history=validation_history,
        )
        summarizer_raw_response = agent_runner(summarizer_prompt)
        assisted_repair_request = apply_assisted_repair_summarizer_agent_response(
            summarizer_raw_response,
            fallback_request,
        )
        if assisted_repair_request is None:
            assisted_repair_request = fallback_request
            debug_result: dict[str, object] = {
                "source": "deterministic_fallback",
                "fallback_reason": "assisted_repair_summarizer_invalid_json",
                **fallback_request,
            }
        else:
            debug_result = {
                "source": "agent",
                **assisted_repair_request,
            }
        agent_layer_calls.append(
            self._agent_layer_call(
                phase="assisted_repair_summarizer",
                agent_prompt=summarizer_prompt,
                raw_response=summarizer_raw_response,
                result_key="assisted_repair_request",
                result=debug_result,
            )
        )
        return assisted_repair_request

    def _first_validation_finding(self, validation_pass: dict[str, object]) -> dict[str, object] | None:
        for report_key in ("format_report", "syntax_report", "static_report", "principle_report", "rule_report"):
            report = validation_pass.get(report_key)
            if not isinstance(report, dict):
                continue
            findings = report.get("findings")
            if isinstance(findings, list) and findings:
                first_finding = findings[0]
                if isinstance(first_finding, dict):
                    return first_finding
        return None

    def _failure_classes_from_validation_pass(self, validation_pass: dict[str, object]) -> list[str]:
        classes: list[str] = []
        for report_key in ("format_report", "syntax_report", "static_report", "principle_report", "rule_report"):
            report = validation_pass.get(report_key)
            if not isinstance(report, dict):
                continue
            findings = report.get("findings")
            if not isinstance(findings, list):
                continue
            for finding in findings:
                if not isinstance(finding, dict):
                    continue
                failure_class = finding.get("failure_class")
                if isinstance(failure_class, str) and failure_class and failure_class not in classes:
                    classes.append(failure_class)
        return classes

    def _assisted_repair_summary(
        self,
        primary_finding: dict[str, object] | None,
        critic_report: dict[str, object],
        language: str,
    ) -> str:
        message = None
        if primary_finding is not None:
            message = primary_finding.get("message")
        if not isinstance(message, str) or not message.strip():
            message = critic_report.get("message")
        if isinstance(message, str) and message.strip():
            return self._localize_assisted_repair_text(message, language)
        if language == "en":
            return "The candidate still fails deterministic validation after the short repair loop."
        return "Кандидат всё ещё не проходит детерминированную проверку после короткого repair loop."

    def _assisted_repair_options(
        self,
        primary_finding: dict[str, object] | None,
        critic_report: dict[str, object],
        language: str,
    ) -> list[dict[str, str]]:
        failure_class = ""
        suggestion = None
        if primary_finding is not None:
            raw_failure_class = primary_finding.get("failure_class")
            if isinstance(raw_failure_class, str):
                failure_class = raw_failure_class
            raw_suggestion = primary_finding.get("suggestion")
            if isinstance(raw_suggestion, str) and raw_suggestion.strip():
                suggestion = self._localize_assisted_repair_text(raw_suggestion, language)
        if suggestion is None:
            repair_prompt = critic_report.get("repair_prompt")
            if isinstance(repair_prompt, str) and repair_prompt.strip():
                suggestion = self._localize_assisted_repair_text(repair_prompt, language)

        if language == "en":
            fallback_label = "Follow validator hint"
            fallback_effect = suggestion or "Regenerate the candidate with the validator hint applied explicitly."
            simplify_label = "Simplify the result"
            simplify_effect = "Keep the same user goal, but prefer a simpler result shape and avoid extra structure."
            custom_label = "Custom instruction"
            custom_effect = "Enter your own instruction for the next wide repair iteration."
        else:
            fallback_label = "Учесть замечание валидатора"
            fallback_effect = suggestion or "Перегенерировать кандидат с явным учётом замечания валидации."
            simplify_label = "Упростить результат"
            simplify_effect = "Сохранить цель пользователя, но выбрать более простую форму результата и убрать лишнюю структуру."
            custom_label = "Свой вариант"
            custom_effect = "Пользователь вводит свою инструкцию для следующей широкой итерации."

        primary_option = {
            "id": "follow_validator_hint",
            "label": fallback_label,
            "effect": fallback_effect,
        }
        if failure_class in {"missing_array_allocator", "missing_filter_result_container"}:
            primary_option = {
                "id": "use_lowcode_array",
                "label": "_utils.array.new()",
                "effect": suggestion or (
                    "Перегенерировать с явным требованием сохранить array semantics через _utils.array.new()."
                    if language != "en"
                    else "Regenerate with an explicit requirement to preserve array semantics via _utils.array.new()."
                ),
            }
        elif failure_class == "markdown_fence":
            primary_option = {
                "id": "return_plain_output",
                "label": "Убрать markdown" if language != "en" else "Remove markdown",
                "effect": suggestion or (
                    "Вернуть только чистый результат без markdown и пояснений."
                    if language != "en"
                    else "Return only the plain result without markdown or extra prose."
                ),
            }
        elif failure_class in {"invalid_wrapper", "non_string_lua_value"}:
            primary_option = {
                "id": "keep_lowcode_wrapper",
                "label": "Сохранить lua{...}lua" if language != "en" else "Keep lua{...}lua",
                "effect": suggestion or (
                    "Сохранить LowCode JSON-контракт и обернуть код в lua{...}lua."
                    if language != "en"
                    else "Keep the LowCode JSON contract and wrap generated code in lua{...}lua."
                ),
            }
        elif failure_class in {"patch_path_keys", "full_rewrite_patch_payload"}:
            primary_option = {
                "id": "keep_additive_patch",
                "label": "Только additive patch" if language != "en" else "Additive patch only",
                "effect": suggestion or (
                    "Вернуть только additive patch без переписывания всего payload."
                    if language != "en"
                    else "Return only an additive patch without rewriting the full payload."
                ),
            }

        return [
            primary_option,
            {
                "id": "simplify_result",
                "label": simplify_label,
                "effect": simplify_effect,
            },
            {
                "id": "custom",
                "label": custom_label,
                "effect": custom_effect,
            },
        ]

    def _localize_assisted_repair_text(self, text: str | None, language: str) -> str:
        if not isinstance(text, str):
            return ""

        stripped = text.strip()
        if not stripped:
            return ""

        normalized_language = normalize_language(language)
        localized = _localize_critic_text(stripped, normalized_language)
        if normalized_language == "en" or localized != stripped:
            return localized

        if stripped.endswith(" output must not include markdown fences."):
            output_mode = stripped.removesuffix(" output must not include markdown fences.")
            return f"{output_mode} не должен содержать markdown-ограждения."
        if stripped.startswith("Return a plain JSON object for ") and stripped.endswith(" mode."):
            output_mode = stripped.removeprefix("Return a plain JSON object for ").removesuffix(" mode.")
            return f"Верни чистый JSON object для режима {output_mode}."
        if stripped == "Return only Lua code without markdown fences or surrounding prose.":
            return "Верни только Lua-код без markdown-ограждений и пояснений."

        return stripped

    def _agent_layer_call(
        self,
        *,
        phase: str,
        agent_prompt: AgentPrompt,
        raw_response: str,
        result_key: str,
        result: dict[str, object],
    ) -> dict[str, object]:
        return {
            "phase": phase,
            "agent": agent_prompt.agent_name,
            "prompt": agent_prompt.to_legacy_prompt(),
            "messages": agent_prompt.to_messages_payload(),
            "raw_response": raw_response,
            result_key: result,
        }

    def _model_call(
        self,
        *,
        phase: str,
        agent: str,
        prompt: str,
        raw_response: str,
        truncation_guard: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "phase": phase,
            "agent": agent,
            "prompt": prompt,
            "raw_response": raw_response,
        }
        if truncation_guard is not None:
            payload["truncation_guard"] = truncation_guard
        return payload

    def _emit_progress(self, progress_callback: Callable[[str], None] | None, stage: str) -> None:
        if progress_callback is not None:
            progress_callback(stage)

    def _generate_from_prompt_with_continuation_guard(
        self,
        *,
        model_adapter: OllamaModelAdapter,
        prompt: str,
        phase: str,
        temporary_paths: list[Path],
    ) -> _PromptGeneration:
        temp_path = self._create_generator_temp_file(phase=phase, temporary_paths=temporary_paths)
        metadata_runner = getattr(model_adapter, "generate_from_prompt_with_metadata", None)
        if not callable(metadata_runner):
            response = model_adapter.generate_from_prompt(prompt)
            temp_path.write_text(response, encoding="utf-8")
            return _PromptGeneration(response=response, temporary_paths=(temp_path,))

        first_payload = metadata_runner(prompt)
        first_chunk = str(first_payload["response"])
        temp_path.write_text(first_chunk, encoding="utf-8")
        if not self._prompt_payload_hit_num_predict(first_payload):
            return _PromptGeneration(response=first_chunk, temporary_paths=(temp_path,))

        chunks: list[dict[str, object]] = []
        chunks.append(self._truncation_chunk_metadata(index=1, payload=first_payload))
        current_payload = first_payload
        continuation_count = 0

        while (
            self._prompt_payload_hit_num_predict(current_payload)
            and continuation_count < _MAX_GENERATOR_CONTINUATIONS
        ):
            continuation_count += 1
            accumulated = temp_path.read_text(encoding="utf-8")
            continuation_prompt = self._generator_continuation_prompt(
                original_prompt=prompt,
                accumulated_candidate=accumulated,
                continuation_count=continuation_count,
            )
            current_payload = metadata_runner(continuation_prompt)
            next_chunk = str(current_payload["response"])
            with temp_path.open("a", encoding="utf-8") as file_handle:
                file_handle.write(next_chunk)
            chunks.append(self._truncation_chunk_metadata(index=len(chunks) + 1, payload=current_payload))

        return _PromptGeneration(
            response=temp_path.read_text(encoding="utf-8"),
            truncation_guard={
                "continuation_count": continuation_count,
                "limit_reached": self._prompt_payload_hit_num_predict(current_payload),
                "chunks": chunks,
                "temporary_file_used": True,
            },
            temporary_paths=(temp_path,),
        )

    def _create_generator_temp_file(self, *, phase: str, temporary_paths: list[Path]) -> Path:
        temp_file = tempfile.NamedTemporaryFile(
            "w+",
            encoding="utf-8",
            delete=False,
            prefix=f"luamts_{phase}_",
            suffix=".txt",
        )
        try:
            temp_path = Path(str(temp_file.name))
        finally:
            temp_file.close()
        temporary_paths.append(temp_path)
        return temp_path

    def _cleanup_temporary_paths(self, temporary_paths: list[Path]) -> None:
        for temp_path in temporary_paths:
            temp_path.unlink(missing_ok=True)

    def _prompt_payload_hit_num_predict(self, payload: dict[str, object]) -> bool:
        eval_count = payload.get("eval_count")
        num_predict = payload.get("num_predict")
        return isinstance(eval_count, int) and isinstance(num_predict, int) and eval_count >= num_predict

    def _truncation_chunk_metadata(self, *, index: int, payload: dict[str, object]) -> dict[str, object]:
        chunk_metadata: dict[str, object] = {
            "index": index,
            "truncated": self._prompt_payload_hit_num_predict(payload),
        }
        eval_count = payload.get("eval_count")
        if isinstance(eval_count, int):
            chunk_metadata["eval_count"] = eval_count
        num_predict = payload.get("num_predict")
        if isinstance(num_predict, int):
            chunk_metadata["num_predict"] = num_predict
        return chunk_metadata

    def _generator_continuation_prompt(
        self,
        *,
        original_prompt: str,
        accumulated_candidate: str,
        continuation_count: int,
    ) -> str:
        return "\n\n".join(
            [
                original_prompt,
                "Продолжение обрезанной генерации:",
                (
                    "Предыдущая генерация упёрлась в лимит вывода num_predict и была обрезана. "
                    f"Это попытка продолжения {continuation_count}. "
                    "Ниже уже сгенерированная часть ответа generator."
                ),
                accumulated_candidate,
                (
                    "Дополни только недостающую часть ответа. Не повторяй уже выведенный фрагмент. "
                    "Итог после склейки должен быть одним корректным JSON object по LowCode-контракту. "
                    "Верни только продолжение, без markdown, пояснений или текста вокруг."
                ),
            ]
        )

    def _build_repair_prompt_builder_result(
        self,
        *,
        model_adapter: OllamaModelAdapter,
        planner_result: PlannerResult,
        original_prompt_builder_result: PromptBuilderResult,
        current_candidate: str,
        repair_instruction: str,
        validation_pass: dict[str, object],
        repair_count: int,
        agent_layer_calls: list[dict[str, object]],
        progress_callback: Callable[[str], None] | None,
    ) -> PromptBuilderResult:
        fallback_result = build_lowcode_repair_prompt_builder_result(
            original_result=original_prompt_builder_result,
            current_candidate=current_candidate,
            repair_instruction=repair_instruction,
            validation_pass=validation_pass,
            repair_count=repair_count,
        )
        _ = (model_adapter, planner_result, agent_layer_calls, progress_callback)
        return fallback_result

    def _validator_report(self, validation_passes: list[object], final_status: str) -> dict[str, object]:
        return {
            "status": "pass" if final_status in {"passed", "repaired"} else "fail",
            "iterations": validation_passes,
        }

    def _run_deterministic_validation(
        self,
        *,
        candidate: str,
        prompt: str,
        prompt_builder_result: PromptBuilderResult,
        planner_result: PlannerResult,
        model_adapter: OllamaModelAdapter,
        model_calls: list[dict[str, object]],
        repair_count: int,
        phase: str,
    ) -> dict[str, object]:
        normalized_candidate, format_report, syntax_report, static_report, principle_report, rule_report = (
            run_validation_pipeline(
                candidate,
                output_mode=LOWCODE_JSON,
                allowed_data_roots=planner_result.input_roots,
                forbidden_patterns=prompt_builder_result.forbidden_patterns,
                risk_tags=planner_result.task_spec.risk_tags,
                archetype=planner_result.task_spec.archetype,
                task_spec=planner_result.task_spec,
            )
        )
        runtime_report = self._skipped_validator_report("runtime_validator", "not_in_current_slice")
        semantic_report = self._skipped_validator_report("semantic_validator", "deterministic_validation_failed")
        deterministic_pass = format_report.status == "pass" and rule_report.status == "pass"
        semantic_validation_ran = False
        if deterministic_pass:
            semantic_agent_prompt = build_semantic_critic_agent_prompt(
                prompt=prompt,
                candidate=normalized_candidate or candidate.strip(),
                output_mode=LOWCODE_JSON,
                task_spec=planner_result.task_spec,
                format_report=format_report,
                syntax_report=syntax_report,
                static_report=static_report,
                principle_report=principle_report,
                runtime_report=runtime_report,
                language=planner_result.language,
            )
            semantic_raw_response = model_adapter.generate_from_agent(semantic_agent_prompt)
            semantic_report = parse_semantic_critic_response(semantic_raw_response)
            semantic_validation_ran = True
            model_calls.append(
                {
                    "phase": "semantic_validation",
                    "agent": semantic_agent_prompt.agent_name,
                    "prompt": semantic_agent_prompt.to_legacy_prompt(),
                    "messages": semantic_agent_prompt.to_messages_payload(),
                    "raw_response": semantic_raw_response,
                    "semantic_report": semantic_report.to_dict(),
                }
            )
        pass_status = deterministic_pass and semantic_report.status == "pass"
        validation_bundle = self._validation_bundle(
            candidate=candidate,
            planner_result=planner_result,
            format_report=format_report,
            syntax_report=syntax_report,
            static_report=static_report,
            principle_report=principle_report,
            runtime_report=runtime_report,
            semantic_report=semantic_report,
            rule_report=rule_report,
        )
        critic_report = build_critic_report(
            format_report,
            syntax_report,
            static_report,
            principle_report,
            runtime_report,
            semantic_report,
            output_mode=LOWCODE_JSON,
            repair_count=repair_count,
            clarification_count=0,
            repeated_failure_class=False,
            oscillation_detected=False,
            task_intents=planner_result.task_intents,
            language=planner_result.language,
            validation_bundle=validation_bundle,
        )
        validation_pass = self._validation_pass_payload(
            candidate=candidate,
            normalized_candidate=normalized_candidate,
            phase=phase,
            format_report=format_report,
            syntax_report=syntax_report,
            static_report=static_report,
            principle_report=principle_report,
            runtime_report=runtime_report,
            semantic_report=semantic_report,
            rule_report=rule_report,
            critic_report=critic_report,
        )
        stop_reason = "passed"
        if not deterministic_pass:
            stop_reason = "deterministic_validation_failed"
        elif semantic_report.status != "pass":
            stop_reason = "semantic_validation_failed"
        return {
            "validation_status": "passed" if pass_status else "failed",
            "stop_reason": stop_reason,
            "validator_report": {
                "status": "pass" if pass_status else "fail",
                "iterations": [validation_pass],
            },
            "critic_report": critic_report,
            "validation_passes": [validation_pass],
            "semantic_validation_ran": semantic_validation_ran,
        }

    def _validation_bundle(
        self,
        *,
        candidate: str,
        planner_result: PlannerResult,
        format_report: ValidatorReport,
        syntax_report: ValidatorReport,
        static_report: ValidatorReport,
        principle_report: ValidatorReport,
        runtime_report: ValidatorReport,
        semantic_report: ValidatorReport,
        rule_report: ValidatorReport,
    ) -> ValidationBundle:
        final_failure_classes = self._collect_failure_classes(
            format_report,
            syntax_report,
            static_report,
            principle_report,
            rule_report,
            runtime_report,
            semantic_report,
        )
        return ValidationBundle(
            task_spec=planner_result.task_spec,
            current_candidate=candidate,
            format_report=format_report,
            syntax_report=syntax_report,
            static_report=static_report,
            principle_report=principle_report,
            runtime_report=runtime_report,
            semantic_report=semantic_report,
            final_failure_classes=final_failure_classes,
            repair_priority=final_failure_classes,
            invalid_shape_signature=self._findings_signature(format_report, syntax_report, static_report),
            disallowed_root_signature=self._findings_signature(static_report, principle_report),
        )

    def _skipped_validator_report(self, validator: str, reason: str) -> ValidatorReport:
        return ValidatorReport(validator=validator, status="skipped", skipped_reason=reason)

    def _collect_failure_classes(self, *reports: ValidatorReport) -> tuple[str, ...]:
        failure_classes: list[str] = []
        for report in reports:
            for finding in report.findings:
                if finding.failure_class not in failure_classes:
                    failure_classes.append(finding.failure_class)
        return tuple(failure_classes)

    def _findings_signature(self, *reports: ValidatorReport) -> str | None:
        parts: list[str] = []
        for report in reports:
            for finding in report.findings:
                signature = f"{finding.failure_class}:{finding.location}:{finding.message}"
                if signature not in parts:
                    parts.append(signature)
        return "|".join(parts) if parts else None

    def _validation_pass_payload(
        self,
        *,
        candidate: str,
        normalized_candidate: str | None,
        phase: str,
        format_report: ValidatorReport,
        syntax_report: ValidatorReport,
        static_report: ValidatorReport,
        principle_report: ValidatorReport,
        runtime_report: ValidatorReport,
        semantic_report: ValidatorReport,
        rule_report: ValidatorReport,
        critic_report: dict[str, object],
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "phase": phase,
            "candidate": candidate,
            "format_report": format_report.to_dict(),
            "syntax_report": syntax_report.to_dict(),
            "static_report": static_report.to_dict(),
            "principle_report": principle_report.to_dict(),
            "runtime_report": runtime_report.to_dict(),
            "semantic_report": semantic_report.to_dict(),
            "rule_report": rule_report.to_dict(),
            "critic_report": critic_report,
        }
        if normalized_candidate is not None:
            payload["normalized_candidate"] = normalized_candidate
        return payload

    def _build_debug_payload(
        self,
        prompt_builder_result: PromptBuilderResult,
        prompt: str,
        raw_response: str,
        agent_layer_calls: list[dict[str, object]],
        planner_result: PlannerResult,
        validation_passes: list[object],
        model_calls: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "prompt_package": {
                "prompt": prompt,
                "expected_result_format": LOWCODE_LUA_EXPECTED_RESULT_FORMAT,
                "planner_result": planner_result.to_debug_dict(),
                "prompt_builder_result": prompt_builder_result.to_debug_dict(),
                "task_spec": planner_result.task_spec.to_dict(),
            },
            "pipeline_layers": [
                *[
                    {
                        "stage": str(call["phase"]),
                        "kind": "llm_prompt",
                        "status": "completed",
                        "agent": str(call["agent"]),
                    }
                    for call in agent_layer_calls
                ],
                *[
                    {
                        "stage": self._model_call_layer_stage(str(call.get("phase") or "")),
                        "kind": "llm_prompt",
                        "status": "completed",
                        "agent": str(call["agent"]),
                    }
                    for call in model_calls
                    if call.get("phase") != "semantic_validation"
                ],
                {
                    "stage": "deterministic_validation",
                    "kind": "deterministic",
                    "status": "completed",
                    "agent": "validator",
                },
                *[
                    {
                        "stage": self._model_call_layer_stage(str(call.get("phase") or "")),
                        "kind": "llm_prompt",
                        "status": "completed",
                        "agent": str(call["agent"]),
                    }
                    for call in model_calls
                    if call.get("phase") == "semantic_validation"
                ],
            ],
            "agent_layer_calls": agent_layer_calls,
            "model_calls": model_calls,
            "validation_passes": validation_passes,
        }

    def _model_call_layer_stage(self, phase: str) -> str:
        if phase == "generation":
            return "generator"
        if phase == "repair_generation":
            return "repair_generator"
        if phase == "semantic_validation":
            return "semantic_critic"
        return phase

    def _adapter_for_request(
        self,
        *,
        mode: str,
        model: str | None,
        runtime_options: dict[str, int | float] | RuntimeOptions | None,
        allow_cloud_model: bool,
    ) -> OllamaModelAdapter:
        normalized_mode = normalize_mode(mode)
        if (
            normalized_mode == RELEASE_MODE
            and model is None
            and runtime_options is None
            and not allow_cloud_model
        ):
            return self._model_adapter

        options = runtime_options if isinstance(runtime_options, RuntimeOptions) else RuntimeOptions.from_mapping(runtime_options)
        return self._model_adapter.with_overrides(
            model=model,
            runtime_options=options,
            mode=normalized_mode,
            allow_cloud_model=allow_cloud_model,
        )
