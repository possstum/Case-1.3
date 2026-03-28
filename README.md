# Lyrics-to-Song Demo

## Demo links
- GitHub: https://github.com/possstum/Case-1.3
- UI: https://case-13--greg-og.replit.app/ui
- Docs: https://case-13--greg-og.replit.app/docs

## Result
The project demonstrates an end-to-end lyrics-to-song generation workflow:
- control-plane with web UI and REST API
- async job submission and polling
- final track generation on an H100 GPU runner using YuE

Generated artifacts:
- mixed track
- vocal stem
- instrumental stem

Note: in the attached sample, the vocal section begins after a relatively long intro, but the full generation pipeline is functioning end-to-end.

# lyrics2song service

Monorepo for the `lyrics2song` service. The control-plane accepts and tracks jobs, while the GPU worker is deployed separately and performs model-side work on GPU-capable infrastructure.

## Hosting model

- Replit hosts only `apps/control-plane`.
- `apps/gpu-worker` is deployed separately on GPU-capable infrastructure.
- `infra/helm` contains Kubernetes manifests for running both services together, with KEDA autoscaling the GPU worker.

## Repository layout

```text
apps/
  control-plane/   FastAPI API surface and orchestration entrypoint
  gpu-worker/      Python worker that runs separately from Replit
infra/
  helm/            Helm chart for Kubernetes + KEDA deployment
data/
  validator/       Dataset validation CLI
finetune/
  Makefile         Env-driven YuE fine-tune workflow
  scripts/         Fine-tuning wrapper scripts
```

## Control-plane service

Implemented in `apps/control-plane` as a FastAPI service with:

- `GET /healthz`
- `POST /v1/jobs`
- `GET /v1/jobs/{job_id}`
- `GET /v1/jobs/{job_id}/result`
- `GET /v1/models`
- `GET /v1/rate-limits`
- `WS /v1/ws` as a WebSocket progress stream

Runtime features:

- metadata storage in SQLite for dev and Postgres via `DATABASE_URL` for prod
- Redis + RQ queueing with a separate GPU worker process
- `X-API-Key` auth with optional Bearer JWT validation
- simple token-bucket rate limiting per API key

## Local run commands

Prerequisite: use Python 3.11 locally, or run via Docker. The codebase uses modern type syntax such as `str | None`, which is not compatible with Python 3.9.

### 1. Local control-plane stack

```bash
docker compose build control-plane
docker compose up -d postgres redis minio
docker compose run --rm minio-init
docker compose up -d control-plane
```

Expected local host ports:

- Postgres: `127.0.0.1:15432`
- Redis: `127.0.0.1:16379`
- MinIO: `http://127.0.0.1:9000`
- Control-plane: `http://127.0.0.1:8000`

The local machine runs only `postgres`, `redis`, `minio`, and `control-plane`.
The Compose `gpu-worker` service is opt-in behind the `gpu` profile and is not part of the default local stack.

Health check:

```bash
curl http://127.0.0.1:8000/healthz
curl -H 'X-API-Key: dev-api-key' http://127.0.0.1:8000/v1/models
```

Minimal web UI is served from `http://127.0.0.1:8000/`.

If you only want to smoke-test the API surface without Redis/RQ, set `QUEUE_MODE=inline`.
In `QUEUE_MODE=inline`, the control-plane now simulates completion itself and serves demo WAV artifacts from local `/artifacts/...` URLs, which is enough to exercise the web UI end to end.

### 2. Control-plane outside Docker (optional)

```bash
cd apps/control-plane
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export API_KEYS=dev-api-key
export DATABASE_URL='postgresql+psycopg://lyrics2song:lyrics2song@127.0.0.1:15432/lyrics2song'
export REDIS_URL=redis://127.0.0.1:16379/0
export QUEUE_NAME=lyrics2song-jobs
export QUEUE_MODE=rq
export RESULT_DELIVERY=public
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. Control-plane tests

```bash
cd apps/control-plane
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python3 -m pytest tests -q
```

### 4. GPU host worker

Run the GPU worker on a separate GPU-capable host. Point it at the local control-plane machine through the exposed host ports above. Replace `LOCAL_HOST` if the control-plane machine's LAN IP changes.

```bash
cd apps/gpu-worker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export LOCAL_HOST=192.168.0.61
export DATABASE_URL='postgresql+psycopg://lyrics2song:lyrics2song@'"$LOCAL_HOST"':15432/lyrics2song'
export REDIS_URL='redis://'"$LOCAL_HOST"':16379/0'
export QUEUE_NAME=lyrics2song-jobs
export PROGRESS_CHANNEL=lyrics2song-job-progress
export S3_BUCKET=lyrics2song-results
export S3_ENDPOINT_URL='http://'"$LOCAL_HOST"':9000'
export S3_PUBLIC_BASE_URL='http://'"$LOCAL_HOST"':9000/lyrics2song-results'
export S3_ACCESS_KEY_ID=minioadmin
export S3_SECRET_ACCESS_KEY=minioadmin
export S3_FORCE_PATH_STYLE=true
export S3_PREFIX=results
export YUE_RUNNER=cli
export YUE_WORKDIR=/ABS/PATH/TO/YuE/inference
export YUE_INFER_SCRIPT=infer.py
export YUE_CUDA_IDX=0
python3 -c "import torch; print(torch.cuda.is_available(), torch.cuda.device_count())"
python3 "$YUE_WORKDIR/$YUE_INFER_SCRIPT" --help
python3 worker.py
```

### 5. End-to-end verification

Once the GPU host worker is running, submit a job from the local control-plane and wait for completion:

```bash
JOB_ID=$(curl -sS -H 'X-API-Key: dev-api-key' \
  -H 'Content-Type: application/json' \
  -d '{"lyrics":"We walked into the night","tags":["dream-pop","female vocal"],"duration":60,"model_name":"lyrics2song-demo-v1"}' \
  http://127.0.0.1:8000/v1/jobs | jq -r '.job_id')

echo "$JOB_ID"

until [ "$(curl -sS -H 'X-API-Key: dev-api-key' http://127.0.0.1:8000/v1/jobs/$JOB_ID | jq -r '.status')" = "completed" ]; do
  sleep 5
done

curl -sS -H 'X-API-Key: dev-api-key' http://127.0.0.1:8000/v1/jobs/$JOB_ID
RESULT_URL=$(curl -sS -H 'X-API-Key: dev-api-key' "http://127.0.0.1:8000/v1/jobs/$JOB_ID/result?redirect=false" | jq -r '.result_url')
echo "$RESULT_URL"
curl -I "$RESULT_URL"
```

Accepted job payload fields:

- `lyrics` required
- `tags` or `genre_tags` required unless `style_prompt` is provided
- `duration` in seconds
- `segments` optional alias for explicit YuE `run_n_segments`
- `seed` optional
- `reference_audio_url` optional for single-track ICL prompt
- `reference_vocal_url` + `reference_instrumental_url` optional for explicit dual-track ICL
- `dual_track=true` to enable YuE `--use_dual_tracks_prompt`

If `reference_audio_url` is passed together with `dual_track=true`, the worker will resolve/download `vocals` and `instrumental` tracks from either:

- a JSON manifest URL containing vocal/instrumental URLs
- explicit `reference_vocal_url` + `reference_instrumental_url`
- a prefix/path that exposes `vocals.mp3` and `instrumental.mp3`

### 6. Dataset validator

```bash
cd data/validator
python3 cli.py /path/to/dataset.jsonl
python3 validate_yue_jsonl.py /path/to/yue_dataset.jsonl
```

Expected JSONL fields per row: `artist`, `title`, `lyrics`.

`validate_yue_jsonl.py` validates YuE metadata rows, checks required fields
(`codec`, `vocals_codec`, `instrumental_codec`, `audio_length_in_sec`, `msa`,
`genres`, `splitted_lyrics.segmented_lyrics`), verifies referenced `.npy` files,
checks `codec_frame_start < codec_frame_end`, validates `offset`/`duration`
values against `audio_length_in_sec`, prints a terminal summary, and writes a
JSON report next to the input file by default.

### 7. Helm charts

Combined control-plane + GPU worker chart:

```bash
helm template lyrics2song infra/helm/lyrics2song
helm upgrade --install lyrics2song infra/helm/lyrics2song \
  --namespace lyrics2song \
  --create-namespace
```

Standalone GPU worker chart with GPU scheduling and KEDA `ScaledJob`:

```bash
helm template gpu-worker infra/helm/gpu-worker
helm upgrade --install gpu-worker infra/helm/gpu-worker \
  --namespace gpu-workers \
  --create-namespace
```

The standalone chart defaults to `scaledJob.enabled=true`, `minReplicaCount=0`,
`maxReplicaCount=20`, and `pollingInterval=5`. For a long-lived worker pod
instead of queue-driven jobs, switch to:

```bash
helm upgrade --install gpu-worker infra/helm/gpu-worker \
  --namespace gpu-workers \
  --create-namespace \
  --set deployment.enabled=true \
  --set scaledJob.enabled=false
```

`scaledJob.trigger.type` supports `redis` and `rabbitmq`. Note that the current
`apps/gpu-worker` implementation still consumes jobs from Redis/RQ; RabbitMQ is
therefore only a valid autoscaling signal if your image/queue flow is adapted to
match it.

Note: KEDA CRDs/operators must already be installed in the cluster.

### 8. Fine-tune wrappers

```bash
cd finetune
cp .env.example .env
# edit .env with your YuE paths and dataset/model config
chmod +x scripts/*.sh
make all
```

Available targets:

- `make preprocess` runs YuE `scripts/preprocess_data.sh`
- `make count-tokens` runs YuE `scripts/count_tokens.sh`
- `make parse-mixture` runs YuE `core/parse_mixture.py` and saves parsed values to `finetune/out/mixture.env` when it can extract them from stdout
- `make train` runs `finetune/scripts/run_finetune.sh`, loading parameters from `finetune/.env`
- `make print-config` prints the resolved training configuration without starting the trainer

The provided `.env.example` includes a LoRA-oriented baseline:

- `SEQ_LENGTH=8192`
- `LEARNING_RATE=5e-4`
- `WARMUP_RATIO=0.03`
- `WEIGHT_DECAY=0.01`
- `LORA_R=64`
- `LORA_ALPHA=32`
- `LORA_DROPOUT=0.1`
- `LORA_TARGET_MODULES=q_proj,k_proj,v_proj,o_proj`

`finetune/scripts/run_lora_train.sh` now forwards to `scripts/run_finetune.sh` for compatibility with the previous placeholder interface. `finetune/scripts/package_adapter.sh` remains a thin packaging stub intended to be replaced with your final artifact packaging command.
