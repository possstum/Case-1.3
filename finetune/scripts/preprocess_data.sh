#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

load_env_file

YUE_FINETUNE_DIR="$(resolve_yue_finetune_dir)"
UPSTREAM_SCRIPT="${YUE_PREPROCESS_SCRIPT:-${YUE_FINETUNE_DIR}/scripts/preprocess_data.sh}"

TOKENIZER_MODEL_PATH="${TOKENIZER_MODEL_PATH:-${TOKENIZER_MODEL:-}}"
PREPROCESS_DATASET_NAME="${PREPROCESS_DATASET_NAME:-dummy}"
PREPROCESS_DATASET_TYPE="${PREPROCESS_DATASET_TYPE:-cot}"

require_env TOKENIZER_MODEL_PATH
require_file "${UPSTREAM_SCRIPT}"

TOKENIZER_MODEL_PATH="$(resolve_path "${TOKENIZER_MODEL_PATH}")"

EXTRA_ARGS=()
if [[ -n "${PREPROCESS_EXTRA_ARGS:-}" ]]; then
  # shellcheck disable=SC2206
  EXTRA_ARGS=( ${PREPROCESS_EXTRA_ARGS} )
fi

if [[ $# -gt 0 ]]; then
  EXTRA_ARGS+=("$@")
fi

printf 'Running preprocess step via %s\n' "${UPSTREAM_SCRIPT}"
(
  cd "${YUE_FINETUNE_DIR}"
  bash "${UPSTREAM_SCRIPT}" \
    "${PREPROCESS_DATASET_NAME}" \
    "${PREPROCESS_DATASET_TYPE}" \
    "${TOKENIZER_MODEL_PATH}" \
    "${EXTRA_ARGS[@]}"
)
