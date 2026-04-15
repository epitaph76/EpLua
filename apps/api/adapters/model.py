import os
import subprocess
import sys
from urllib.parse import urlparse
from pathlib import Path

import httpx

from errors import ApiError

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from packages.orchestrator.agent_prompt import AgentMessage, AgentPrompt  # noqa: E402
from packages.orchestrator.domain_adapter import (  # noqa: E402
    build_domain_prompt_package,
    normalize_model_output,
)
from runtime_policy import DEFAULT_MODEL_TAG, RuntimeOptions, enforce_model_policy, normalize_mode  # noqa: E402

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "ollama", "host.docker.internal"}
_DEFAULT_REQUEST_TIMEOUT = 180.0
_TRUNCATION_GUARD_AGENTS = {"planner", "prompter", "semantic_critic"}
_AGENTIC_PROMPT_MARKERS = (
    "planner agent for the luamts validation pipeline",
    "prompter agent for the luamts validation pipeline",
    "semantic critic",
)
_TRUNCATION_RETRY_INSTRUCTION = (
    "Previous output may have been truncated by the token budget. "
    "Retry with only the minimal valid schema, shorter than before, and no commentary."
)


class OllamaModelAdapter:
    def __init__(
        self,
        *,
        http_client: httpx.Client | object | None = None,
        base_url: str | None = None,
        model: str | None = None,
        runtime_options: RuntimeOptions | None = None,
        mode: str | None = None,
        allow_cloud_model: bool = False,
    ) -> None:
        self._base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")
        self._model = model or os.getenv("OLLAMA_MODEL", DEFAULT_MODEL_TAG)
        self._runtime_options = runtime_options or RuntimeOptions.from_env()
        self._mode = normalize_mode(mode)
        self._request_timeout = self._load_request_timeout()
        self._http_client = http_client or httpx.Client()
        self._ensure_local_base_url()
        enforce_model_policy(self._model, mode=self._mode, allow_cloud_model=allow_cloud_model)

    def with_overrides(
        self,
        *,
        model: str | None = None,
        runtime_options: RuntimeOptions | None = None,
        mode: str | None = None,
        allow_cloud_model: bool = False,
    ) -> "OllamaModelAdapter":
        return OllamaModelAdapter(
            http_client=self._http_client,
            base_url=self._base_url,
            model=model or self._model,
            runtime_options=runtime_options or self._runtime_options,
            mode=mode or self._mode,
            allow_cloud_model=allow_cloud_model,
        )

    def generate(
        self,
        task_text: str,
        provided_context: str | None = None,
        *,
        archetype: str | None = None,
        output_mode: str | None = None,
        input_roots: list[str] | None = None,
        risk_tags: list[str] | None = None,
    ) -> str:
        prompt, agent_prompt, effective_output_mode = self._build_prompt(
            task_text,
            provided_context,
            archetype=archetype,
            output_mode=output_mode,
            input_roots=input_roots,
            risk_tags=risk_tags,
        )

        response_text = self.generate_from_agent(agent_prompt) if agent_prompt is not None else self.generate_from_prompt(prompt)
        try:
            if effective_output_mode:
                return normalize_model_output(response_text, effective_output_mode)
            return response_text
        except ValueError as exc:
            raise ApiError(
                status_code=422,
                code="domain_contract_error",
                message=str(exc),
            ) from exc

    def generate_from_prompt(self, prompt: str) -> str:
        try:
            payload = self._prompt_completion_payload(prompt)
            if self._should_retry_truncated_prompt_response(prompt, payload):
                payload = self._prompt_completion_payload(self._retry_prompt_text(prompt))
            return str(payload["response"])
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 503:
                return self._generate_via_cli(prompt)
            raise ApiError(
                status_code=502,
                code="model_error",
                message="Local model request failed.",
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiError(
                status_code=502,
                code="model_error",
                message="Local model request failed.",
            ) from exc
        except KeyError as exc:
            raise ApiError(
                status_code=502,
                code="model_error",
                message="Local model response was invalid.",
            ) from exc

    def generate_from_prompt_with_metadata(self, prompt: str) -> dict[str, object]:
        try:
            payload = self._prompt_completion_payload(prompt)
            return {
                "response": str(payload["response"]),
                "eval_count": payload.get("eval_count"),
                "num_predict": self._runtime_options.num_predict,
            }
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 503:
                return {
                    "response": self._generate_via_cli(prompt),
                    "eval_count": None,
                    "num_predict": self._runtime_options.num_predict,
                }
            raise ApiError(
                status_code=502,
                code="model_error",
                message="Local model request failed.",
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiError(
                status_code=502,
                code="model_error",
                message="Local model request failed.",
            ) from exc
        except KeyError as exc:
            raise ApiError(
                status_code=502,
                code="model_error",
                message="Local model response was invalid.",
            ) from exc

    def _prompt_completion_payload(self, prompt: str) -> dict[str, object]:
        response = self._http_client.post(
            f"{self._base_url}/api/generate",
            json={
                "model": self._model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": self._runtime_options.to_ollama_options(),
            },
            timeout=self._request_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise KeyError("response")
        return payload

    def generate_from_agent(self, agent_prompt: AgentPrompt) -> str:
        try:
            payload = self._chat_completion_payload(agent_prompt)
            response_text = self._extract_chat_response_text(payload)
            if self._should_retry_truncated_agent_response(agent_prompt, payload):
                payload = self._chat_completion_payload(self._retry_agent_prompt(agent_prompt))
                response_text = self._extract_chat_response_text(payload)
            if not response_text.strip():
                return self.generate_from_prompt(agent_prompt.to_legacy_prompt())
            return response_text
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {404, 405, 501, 503}:
                return self._generate_via_cli(agent_prompt.to_legacy_prompt()) if exc.response.status_code == 503 else self.generate_from_prompt(agent_prompt.to_legacy_prompt())
            raise ApiError(
                status_code=502,
                code="model_error",
                message="Local model request failed.",
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiError(
                status_code=502,
                code="model_error",
                message="Local model request failed.",
            ) from exc
        except KeyError as exc:
            raise ApiError(
                status_code=502,
                code="model_error",
                message="Local model response was invalid.",
            ) from exc

    def _chat_completion_payload(self, agent_prompt: AgentPrompt) -> dict[str, object]:
        response = self._http_client.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": agent_prompt.to_messages_payload(),
                "stream": False,
                "think": False,
                "options": self._runtime_options.to_ollama_options(),
            },
            timeout=self._request_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise KeyError("message.content")
        return payload

    def _extract_chat_response_text(self, payload: dict[str, object]) -> str:
        message = payload.get("message")
        candidates: list[object] = []
        if isinstance(message, dict):
            candidates.extend(
                message.get(key)
                for key in ("content", "response", "text", "output_text")
            )
        candidates.extend(payload.get(key) for key in ("response", "content", "text", "output_text"))
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        if isinstance(message, dict) and "content" in message:
            return str(message["content"])
        raise KeyError("message.content")

    def _should_retry_truncated_agent_response(
        self,
        agent_prompt: AgentPrompt,
        payload: dict[str, object],
    ) -> bool:
        if agent_prompt.agent_name not in _TRUNCATION_GUARD_AGENTS:
            return False
        eval_count = payload.get("eval_count")
        return isinstance(eval_count, int) and eval_count == self._runtime_options.num_predict

    def _retry_agent_prompt(self, agent_prompt: AgentPrompt) -> AgentPrompt:
        return AgentPrompt(
            agent_name=agent_prompt.agent_name,
            messages=agent_prompt.messages + (AgentMessage(role="user", content=_TRUNCATION_RETRY_INSTRUCTION),),
        )

    def _should_retry_truncated_prompt_response(self, prompt: str, payload: dict[str, object]) -> bool:
        lowered_prompt = prompt.lower()
        if not any(marker in lowered_prompt for marker in _AGENTIC_PROMPT_MARKERS):
            return False
        eval_count = payload.get("eval_count")
        return isinstance(eval_count, int) and eval_count == self._runtime_options.num_predict

    def _retry_prompt_text(self, prompt: str) -> str:
        return prompt + "\n\n" + _TRUNCATION_RETRY_INSTRUCTION

    def _build_prompt(
        self,
        task_text: str,
        provided_context: str | None,
        *,
        archetype: str | None,
        output_mode: str | None,
        input_roots: list[str] | None,
        risk_tags: list[str] | None,
    ) -> tuple[str, AgentPrompt | None, str | None]:
        if archetype or output_mode or input_roots or risk_tags:
            if not archetype or not output_mode:
                raise ValueError("Both archetype and output_mode are required for domain-adapted generation.")
            prompt_package = build_domain_prompt_package(
                task_text,
                provided_context,
                archetype=archetype,
                output_mode=output_mode,
                input_roots=input_roots,
                risk_tags=risk_tags,
                agent_runner=self.generate_from_agent,
            )
            return prompt_package.prompt, prompt_package.agent_prompt, prompt_package.output_mode

        prompt_parts = [task_text]
        if provided_context:
            prompt_parts.append(provided_context)
        return "\n\n".join(prompt_parts), None, None

    def _ensure_local_base_url(self) -> None:
        parsed = urlparse(self._base_url)
        if parsed.hostname not in _LOCAL_HOSTS:
            raise ApiError(
                status_code=500,
                code="configuration_error",
                message="OLLAMA_BASE_URL must point to a local Ollama instance.",
            )

    def _load_request_timeout(self) -> float:
        raw_timeout = os.getenv("OLLAMA_REQUEST_TIMEOUT")
        if raw_timeout is None:
            return _DEFAULT_REQUEST_TIMEOUT

        try:
            timeout = float(raw_timeout)
        except ValueError:
            return _DEFAULT_REQUEST_TIMEOUT

        if timeout <= 0:
            return _DEFAULT_REQUEST_TIMEOUT
        return timeout

    def _generate_via_cli(self, prompt: str) -> str:
        try:
            result = subprocess.run(
                ["ollama", "run", "--think=false", self._model, prompt],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise ApiError(
                status_code=502,
                code="model_error",
                message="Local model request failed.",
            ) from exc

        return result.stdout.strip()
