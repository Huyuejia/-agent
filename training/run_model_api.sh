#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

export HF_HOME="${HF_HOME:-training/.cache/huggingface}"
export MODEL_BASE_NAME="${MODEL_BASE_NAME:-Qwen/Qwen2.5-1.5B-Instruct}"
export MODEL_ADAPTER_PATH="${MODEL_ADAPTER_PATH:-training/runs/qlora-20260627/checkpoints/lora-adapter/final}"

exec training/.venv/bin/python -m uvicorn training.model_api:app \
  --host "${MODEL_HOST:-127.0.0.1}" \
  --port "${MODEL_PORT:-8001}" \
  --log-level "${MODEL_LOG_LEVEL:-info}"
