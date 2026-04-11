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

from packages.orchestrator.domain_adapter import (  # noqa: E402
    build_domain_prompt_package,
    normalize_model_output,
)

_LOCAL_HOSTS = {"127.0.0.1", "localhost"}


class OllamaModelAdapter:
    def __init__(
        self,
        *,
        http_client: httpx.Client | object | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self._base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        self._model = model or os.getenv("OLLAMA_MODEL", "qwen2.5-coder:3b")
        self._http_client = http_client or httpx.Client()
        self._ensure_local_base_url()

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
        prompt, effective_output_mode = self._build_prompt(
            task_text,
            provided_context,
            archetype=archetype,
            output_mode=output_mode,
            input_roots=input_roots,
            risk_tags=risk_tags,
        )

        try:
            response = self._http_client.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "stream": False,
                },
                timeout=60.0,
            )
            response.raise_for_status()
            payload = response.json()
            response_text = str(payload["response"])
            if effective_output_mode:
                return normalize_model_output(response_text, effective_output_mode)
            return response_text
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
        except ValueError as exc:
            raise ApiError(
                status_code=422,
                code="domain_contract_error",
                message=str(exc),
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
    ) -> tuple[str, str | None]:
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
            )
            return prompt_package.prompt, prompt_package.output_mode

        prompt_parts = [task_text]
        if provided_context:
            prompt_parts.append(provided_context)
        return "\n\n".join(prompt_parts), None

    def _ensure_local_base_url(self) -> None:
        parsed = urlparse(self._base_url)
        if parsed.hostname not in _LOCAL_HOSTS:
            raise ApiError(
                status_code=500,
                code="configuration_error",
                message="OLLAMA_BASE_URL must point to a local Ollama instance.",
            )

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
