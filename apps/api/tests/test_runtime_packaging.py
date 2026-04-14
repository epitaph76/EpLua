import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_api_package_exposes_luamts_cli_entrypoint() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "apps" / "api" / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["scripts"]["luamts"] == "cli.main:main"
    assert any(dependency.startswith("rich") for dependency in pyproject["project"]["dependencies"])


def test_docker_compose_pins_ollama_runtime_env_for_release() -> None:
    compose_text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "OLLAMA_NUM_PARALLEL: ${OLLAMA_PARALLEL:-1}" in compose_text
    assert "OLLAMA_NO_CLOUD: ${OLLAMA_NO_CLOUD:-1}" in compose_text
    assert "OLLAMA_NUM_CTX: ${OLLAMA_NUM_CTX:-4096}" in compose_text
    assert "OLLAMA_NUM_PREDICT: ${OLLAMA_NUM_PREDICT:-256}" in compose_text
    assert "OLLAMA_BATCH: ${OLLAMA_BATCH:-1}" in compose_text
    assert "LUAMTS_HISTORY_FILE: /root/.luamts/history" in compose_text
    assert "luamts-api-state:/root/.luamts" in compose_text
    assert "luamts-api-state:" in compose_text
