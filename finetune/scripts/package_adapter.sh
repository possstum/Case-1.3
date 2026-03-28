#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <run-dir> <artifact-dir>"
  exit 1
fi

RUN_DIR="$1"
ARTIFACT_DIR="$2"

echo "Adapter packaging wrapper"
echo "Replace the echo below with your actual packaging command."
echo "tar -czf \"$ARTIFACT_DIR/adapter.tar.gz\" -C \"$RUN_DIR\" ."
