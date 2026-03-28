#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

PRINT_CONFIG=0
PASSTHROUGH_ARGS=()

for arg in "$@"; do
  case "${arg}" in
    --print-config)
      PRINT_CONFIG=1
      ;;
    *)
      PASSTHROUGH_ARGS+=("${arg}")
      ;;
  esac
done

load_env_file

YUE_FINETUNE_DIR="$(resolve_yue_finetune_dir)"
UPSTREAM_SCRIPT="${YUE_RUN_FINETUNE_SCRIPT:-${YUE_FINETUNE_DIR}/scripts/run_finetune.sh}"
PARSE_MIXTURE_ENV="${PARSE_MIXTURE_ENV:-${FINETUNE_ROOT}/out/mixture.env}"

PARSE_MIXTURE_ENV="$(resolve_path "${PARSE_MIXTURE_ENV}")"
if [[ -f "${PARSE_MIXTURE_ENV}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${PARSE_MIXTURE_ENV}"
  set +a
fi

TOKENIZER_MODEL_PATH="${TOKENIZER_MODEL_PATH:-${TOKENIZER_MODEL:-}}"
DATA_PATH="${DATA_PATH:-}"
DATA_CACHE_PATH="${DATA_CACHE_PATH:-./cache/data}"
DATA_SPLIT="${DATA_SPLIT:-900,50,50}"
MODEL_NAME="${MODEL_NAME:-m-a-p/YuE-s1-7B-anneal-en-cot}"
MODEL_CACHE_DIR="${MODEL_CACHE_DIR:-./cache/models}"
OUTPUT_DIR="${OUTPUT_DIR:-./out/run-001}"
SEQ_LENGTH="${SEQ_LENGTH:-8192}"
LEARNING_RATE="${LEARNING_RATE:-5e-4}"
WARMUP_RATIO="${WARMUP_RATIO:-0.03}"
WEIGHT_DECAY="${WEIGHT_DECAY:-0.01}"
LORA_R="${LORA_R:-64}"
LORA_ALPHA="${LORA_ALPHA:-32}"
LORA_DROPOUT="${LORA_DROPOUT:-0.1}"
LORA_TARGET_MODULES="${LORA_TARGET_MODULES:-q_proj,k_proj,v_proj,o_proj}"
PER_DEVICE_TRAIN_BATCH_SIZE="${PER_DEVICE_TRAIN_BATCH_SIZE:-1}"
GRADIENT_ACCUMULATION_STEPS="${GRADIENT_ACCUMULATION_STEPS:-1}"
NUM_TRAIN_EPOCHS="${NUM_TRAIN_EPOCHS:-10}"

require_file "${UPSTREAM_SCRIPT}"
require_env TOKENIZER_MODEL_PATH
require_env MODEL_NAME
require_env OUTPUT_DIR

if [[ -z "${DATA_PATH}" ]]; then
  printf 'DATA_PATH is empty. Set it in .env or ensure %s exports DATA_PATH.\n' "${PARSE_MIXTURE_ENV}" >&2
  exit 1
fi

TOKENIZER_MODEL_PATH="$(resolve_path "${TOKENIZER_MODEL_PATH}")"
DATA_CACHE_PATH="$(resolve_path "${DATA_CACHE_PATH}")"
MODEL_CACHE_DIR="$(resolve_path "${MODEL_CACHE_DIR}")"
OUTPUT_DIR="$(resolve_path "${OUTPUT_DIR}")"

mkdir -p "${DATA_CACHE_PATH}" "${MODEL_CACHE_DIR}" "${OUTPUT_DIR}"

export TOKENIZER_MODEL="${TOKENIZER_MODEL_PATH}"
export TOKENIZER_MODEL_PATH
export DATA_PATH
export DATA_CACHE_PATH
export DATA_SPLIT
export MODEL_NAME
export MODEL_CACHE_DIR
export OUTPUT_DIR
export SEQ_LENGTH
export MAX_SEQ_LENGTH="${SEQ_LENGTH}"
export LEARNING_RATE
export WARMUP_RATIO
export WEIGHT_DECAY
export LORA_R
export LORA_ALPHA
export LORA_DROPOUT
export LORA_TARGET_MODULES
export TARGET_MODULES="${LORA_TARGET_MODULES}"
export PER_DEVICE_TRAIN_BATCH_SIZE
export GRADIENT_ACCUMULATION_STEPS
export NUM_TRAIN_EPOCHS

printf 'Resolved fine-tune config:\n'
printf '  %-30s %s\n' 'YUE_FINETUNE_DIR' "${YUE_FINETUNE_DIR}"
printf '  %-30s %s\n' 'DATA_PATH' "${DATA_PATH}"
printf '  %-30s %s\n' 'TOKENIZER_MODEL_PATH' "${TOKENIZER_MODEL_PATH}"
printf '  %-30s %s\n' 'MODEL_NAME' "${MODEL_NAME}"
printf '  %-30s %s\n' 'OUTPUT_DIR' "${OUTPUT_DIR}"
printf '  %-30s %s\n' 'SEQ_LENGTH' "${SEQ_LENGTH}"
printf '  %-30s %s\n' 'LEARNING_RATE' "${LEARNING_RATE}"
printf '  %-30s %s\n' 'WARMUP_RATIO' "${WARMUP_RATIO}"
printf '  %-30s %s\n' 'WEIGHT_DECAY' "${WEIGHT_DECAY}"
printf '  %-30s %s\n' 'LORA_R' "${LORA_R}"
printf '  %-30s %s\n' 'LORA_ALPHA' "${LORA_ALPHA}"
printf '  %-30s %s\n' 'LORA_DROPOUT' "${LORA_DROPOUT}"
printf '  %-30s %s\n' 'LORA_TARGET_MODULES' "${LORA_TARGET_MODULES}"

if [[ "${PRINT_CONFIG}" -eq 1 ]]; then
  exit 0
fi

EXTRA_ARGS=()
if [[ -n "${RUN_FINETUNE_ARGS:-}" ]]; then
  # shellcheck disable=SC2206
  EXTRA_ARGS=( ${RUN_FINETUNE_ARGS} )
fi

if [[ ${#PASSTHROUGH_ARGS[@]} -gt 0 ]]; then
  EXTRA_ARGS+=("${PASSTHROUGH_ARGS[@]}")
fi

printf 'Running training via %s\n' "${UPSTREAM_SCRIPT}"
(
  cd "${YUE_FINETUNE_DIR}"
  bash "${UPSTREAM_SCRIPT}" "${EXTRA_ARGS[@]}"
)
