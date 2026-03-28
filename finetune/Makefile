SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

ENV_FILE ?= .env
PYTHON ?= python3
ENV_RUN = set -a; if [[ -f "$(ENV_FILE)" ]]; then source "$(ENV_FILE)"; fi; set +a;

.PHONY: help preprocess count-tokens parse-mixture train all print-config

help:
	@printf '%s\n' \
		'Fine-tune targets:' \
		'  make preprocess     Run YuE finetune/scripts/preprocess_data.sh' \
		'  make count-tokens   Run YuE finetune/scripts/count_tokens.sh' \
		'  make parse-mixture  Run YuE finetune/core/parse_mixture.py and capture env output' \
		'  make train          Run scripts/run_finetune.sh with values from $(ENV_FILE)' \
		'  make all            Run preprocess -> count-tokens -> parse-mixture -> train' \
		'' \
		'Config:' \
		'  cp .env.example .env'

preprocess:
	@$(ENV_RUN) ./scripts/preprocess_data.sh

count-tokens:
	@$(ENV_RUN) ./scripts/count_tokens.sh

parse-mixture:
	@$(ENV_RUN) $(PYTHON) ./scripts/parse_mixture.py

train:
	@$(ENV_RUN) ./scripts/run_finetune.sh

print-config:
	@$(ENV_RUN) ./scripts/run_finetune.sh --print-config

all: preprocess count-tokens parse-mixture train
