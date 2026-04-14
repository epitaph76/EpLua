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

from packages.orchestrator.agent_prompt import AgentPrompt  # noqa: E402
from packages.orchestrator.domain_adapter import (  # noqa: E402
    build_domain_prompt_package,
    normalize_model_output,
)
from runtime_policy import DEFAULT_MODEL_TAG, RuntimeOptions, enforce_model_policy, normalize_mode  # noqa: E402

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "ollama", "host.docker.internal"}
_DEFAULT_REQUEST_TIMEOUT = 180.0


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
            response = self._http_client.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "stream": False,
                    "options": self._runtime_options.to_ollama_options(),
                },
                timeout=self._request_timeout,
            )
            response.raise_for_status()
            payload = response.json()
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

    def generate_from_agent(self, agent_prompt: AgentPrompt) -> str:
        try:
            response = self._http_client.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": self._model,
                    "messages": agent_prompt.to_messages_payload(),
                    "stream": False,
                    "options": self._runtime_options.to_ollama_options(),
                },
                timeout=self._request_timeout,
            )
            response.raise_for_status()
            payload = response.json()
            message = payload["message"]
            if isinstance(message, dict):
                return str(message["content"])
            raise KeyError("message.content")
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
                ["ollama", "run", self._model, prompt],
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
