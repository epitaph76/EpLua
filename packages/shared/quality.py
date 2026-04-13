from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationFinding:
    validator: str
    failure_class: str
    message: str
    location: str
    repairable: bool
    ambiguous: bool = False
    suggestion: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "validator": self.validator,
            "failure_class": self.failure_class,
            "message": self.message,
            "location": self.location,
            "repairable": self.repairable,
            "ambiguous": self.ambiguous,
            "suggestion": self.suggestion,
        }


@dataclass(frozen=True)
class ValidatorReport:
    validator: str
    status: str
    findings: tuple[ValidationFinding, ...] = ()
    normalized_candidate: str | None = None
    skipped_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "validator": self.validator,
            "status": self.status,
            "findings": [finding.to_dict() for finding in self.findings],
        }
        if self.normalized_candidate is not None:
            payload["normalized_candidate"] = self.normalized_candidate
        if self.skipped_reason is not None:
            payload["skipped_reason"] = self.skipped_reason
        return payload


@dataclass(frozen=True)
class ValidationSnapshot:
    phase: str
    format_report: ValidatorReport
    syntax_report: ValidatorReport
    static_report: ValidatorReport
    principle_report: ValidatorReport
    semantic_report: ValidatorReport
    rule_report: ValidatorReport

    def to_dict(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "format_report": self.format_report.to_dict(),
            "syntax_report": self.syntax_report.to_dict(),
            "static_report": self.static_report.to_dict(),
            "principle_report": self.principle_report.to_dict(),
            "semantic_report": self.semantic_report.to_dict(),
            "rule_report": self.rule_report.to_dict(),
        }


@dataclass(frozen=True)
class ValidationSummary:
    status: str
    iterations: tuple[ValidationSnapshot, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "iterations": [snapshot.to_dict() for snapshot in self.iterations],
        }


@dataclass(frozen=True)
class QualityOutcome:
    code: str
    validation_status: str
    trace: tuple[str, ...]
    validator_summary: ValidationSummary
    critic_report: dict[str, object] | None
    repair_count: int
    clarification_count: int
    output_mode: str | None = None
    archetype: str | None = None
    final_candidate_source: str | None = None
    final_candidate_iteration_index: int | None = None
    critic_report_iteration_index: int | None = None
    debug: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "validation_status": self.validation_status,
            "trace": list(self.trace),
            "validator_report": self.validator_summary.to_dict(),
            "critic_report": self.critic_report,
            "repair_count": self.repair_count,
            "clarification_count": self.clarification_count,
            "output_mode": self.output_mode,
            "archetype": self.archetype,
            "debug": self.debug,
        }
        if self.final_candidate_source is not None:
            payload["final_candidate_source"] = self.final_candidate_source
        if self.final_candidate_iteration_index is not None:
            payload["final_candidate_iteration_index"] = self.final_candidate_iteration_index
        if self.critic_report_iteration_index is not None:
            payload["critic_report_iteration_index"] = self.critic_report_iteration_index
        return payload
