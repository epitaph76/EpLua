#!/bin/sh
set -eu

if [ -z "${OLLAMA_MODEL:-}" ]; then
  echo "OLLAMA_MODEL is required." >&2
  exit 1
fi

if ollama list | awk 'NR > 1 { print $1 }' | grep -Fx "$OLLAMA_MODEL" >/dev/null 2>&1; then
  echo "Model already available: $OLLAMA_MODEL"
  exit 0
fi

if [ -n "${OLLAMA_LOCAL_GGUF_BASENAME:-}" ]; then
  MODEL_FILE="/models/${OLLAMA_LOCAL_GGUF_BASENAME}"
  if [ ! -f "$MODEL_FILE" ]; then
    echo "Local GGUF file was not found: $MODEL_FILE" >&2
    exit 1
  fi

  TEMP_MODELFILE="$(mktemp)"
  printf 'FROM %s\n' "$MODEL_FILE" > "$TEMP_MODELFILE"
  echo "Creating Ollama model $OLLAMA_MODEL from $MODEL_FILE"
  ollama create "$OLLAMA_MODEL" -f "$TEMP_MODELFILE"
  rm -f "$TEMP_MODELFILE"
else
  echo "Pulling Ollama model $OLLAMA_MODEL"
  ollama pull "$OLLAMA_MODEL"
fi

echo "Available models after initialization:"
ollama list
