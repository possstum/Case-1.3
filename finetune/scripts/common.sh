#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FINETUNE_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_ENV_FILE="${FINETUNE_ROOT}/.env"

resolve_path() {
  local raw_path="${1:-}"
  if [[ -z "${raw_path}" ]]; then
    return 0
  fi

  if [[ "${raw_path}" = /* ]]; then
    printf '%s\n' "${raw_path}"
    return 0
  fi

  printf '%s\n' "${FINETUNE_ROOT}/${raw_path#./}"
}

load_env_file() {
  local env_file="${ENV_FILE:-${DEFAULT_ENV_FILE}}"
  if [[ -f "${env_file}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${env_file}"
    set +a
  fi
}

require_env() {
  local var_name="$1"
  if [[ -z "${!var_name:-}" ]]; then
    printf 'Missing required env var: %s\n' "${var_name}" >&2
    exit 1
  fi
}

require_file() {
  local file_path="$1"
  if [[ ! -f "${file_path}" ]]; then
    printf 'Required file not found: %s\n' "${file_path}" >&2
    exit 1
  fi
}

ensure_parent_dir() {
  local target_path="$1"
  mkdir -p "$(dirname "${target_path}")"
}

resolve_yue_finetune_dir() {
  local yue_finetune_dir="${YUE_FINETUNE_DIR:-}"
  local yue_workdir="${YUE_WORKDIR:-}"

  if [[ -n "${yue_finetune_dir}" ]]; then
    resolve_path "${yue_finetune_dir}"
    return 0
  fi

  if [[ -n "${yue_workdir}" ]]; then
    printf '%s\n' "$(resolve_path "${yue_workdir}")/finetune"
    return 0
  fi

  printf 'Set YUE_WORKDIR or YUE_FINETUNE_DIR in %s\n' "${ENV_FILE:-${DEFAULT_ENV_FILE}}" >&2
  exit 1
}

python_bin() {
  printf '%s\n' "${PYTHON_BIN:-python3}"
}
