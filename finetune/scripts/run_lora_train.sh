#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -ge 1 ]]; then
  export MODEL_NAME="$1"
fi

if [[ $# -ge 2 ]]; then
  export DATA_PATH="$2"
fi

if [[ $# -ge 3 ]]; then
  export OUTPUT_DIR="$3"
fi

exec "${SCRIPT_DIR}/run_finetune.sh"
