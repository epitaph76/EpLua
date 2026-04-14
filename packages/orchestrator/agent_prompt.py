from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentMessage:
    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {
            "role": self.role,
            "content": self.content,
        }


@dataclass(frozen=True)
class AgentPrompt:
    agent_name: str
    messages: tuple[AgentMessage, ...]

    def to_messages_payload(self) -> list[dict[str, str]]:
        return [message.to_dict() for message in self.messages]

    def to_legacy_prompt(self) -> str:
        sections: list[str] = []
        for message in self.messages:
            sections.extend([f"{message.role.upper()}:", message.content])
        return "\n\n".join(sections)
