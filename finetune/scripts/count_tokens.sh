#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

load_env_file

YUE_FINETUNE_DIR="$(resolve_yue_finetune_dir)"
UPSTREAM_SCRIPT="${YUE_COUNT_TOKENS_SCRIPT:-${YUE_FINETUNE_DIR}/scripts/count_tokens.sh}"
MMAP_DIR="${MMAP_DIR:-${YUE_FINETUNE_DIR}/example/mmap}"

require_file "${UPSTREAM_SCRIPT}"
MMAP_DIR="$(resolve_path "${MMAP_DIR}")"

EXTRA_ARGS=()
if [[ -n "${COUNT_TOKENS_EXTRA_ARGS:-}" ]]; then
  # shellcheck disable=SC2206
  EXTRA_ARGS=( ${COUNT_TOKENS_EXTRA_ARGS} )
fi

if [[ $# -gt 0 ]]; then
  EXTRA_ARGS+=("$@")
fi

printf 'Running token counting via %s\n' "${UPSTREAM_SCRIPT}"
(
  cd "${YUE_FINETUNE_DIR}"
  bash "${UPSTREAM_SCRIPT}" "${MMAP_DIR}" "${EXTRA_ARGS[@]}"
)
