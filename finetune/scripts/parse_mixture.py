#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
FINETUNE_ROOT = SCRIPT_DIR.parent
DEFAULT_ENV_FILE = FINETUNE_ROOT / ".env"


def load_env(env_file: Path) -> None:
    if not env_file.is_file():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def resolve_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return FINETUNE_ROOT / path


def yue_finetune_dir() -> Path:
    if os.getenv("YUE_FINETUNE_DIR"):
        return resolve_path(os.environ["YUE_FINETUNE_DIR"])  # type: ignore[return-value]
    if os.getenv("YUE_WORKDIR"):
        return resolve_path(os.environ["YUE_WORKDIR"]) / "finetune"  # type: ignore[operator]
    raise SystemExit(f"Set YUE_WORKDIR or YUE_FINETUNE_DIR in {os.getenv('ENV_FILE', str(DEFAULT_ENV_FILE))}")


def shell_quote(value: str) -> str:
    return shlex.quote(value)


def parse_assignments(stdout: str) -> dict[str, str]:
    patterns = {
        "DATA_PATH": [
            re.compile(r"^\s*DATA_PATH\s*[:=]\s*(.+)\s*$", re.IGNORECASE),
            re.compile(r"^\s*data\s+path\s*[:=]\s*(.+)\s*$", re.IGNORECASE),
        ],
        "TRAIN_ITERS": [
            re.compile(r"^\s*(?:TRAIN_ITERS|NUM_TRAIN_ITERS?)\s*[:=]\s*(.+)\s*$", re.IGNORECASE),
            re.compile(r"^\s*(?:train\s+iterations|num\s+train\s+iters?)\s*[:=]\s*(.+)\s*$", re.IGNORECASE),
        ],
        "TOTAL_TOKENS": [
            re.compile(r"^\s*(?:TOTAL_TOKENS|TOTAL_TOKEN_COUNT)\s*[:=]\s*(.+)\s*$", re.IGNORECASE),
            re.compile(r"^\s*total\s+tokens?\s*[:=]\s*(.+)\s*$", re.IGNORECASE),
        ],
    }
    extracted: dict[str, str] = {}
    for line in stdout.splitlines():
        for key, regexes in patterns.items():
            for regex in regexes:
                match = regex.match(line)
                if match:
                    extracted[key] = match.group(1).strip()
                    break
            if key in extracted:
                break
    return extracted


def main() -> int:
    env_file = Path(os.getenv("ENV_FILE", DEFAULT_ENV_FILE))
    load_env(env_file)

    finetune_dir = yue_finetune_dir()
    python_bin = os.getenv("PYTHON_BIN", sys.executable)
    script_path = resolve_path(os.getenv("YUE_PARSE_MIXTURE_SCRIPT")) or finetune_dir / "core/parse_mixture.py"
    config_path = resolve_path(os.getenv("MIXTURE_CONFIG")) or finetune_dir / "example/dummy_data_mixture_cfg.yml"
    log_path = resolve_path(os.getenv("PARSE_MIXTURE_LOG", "./out/parse_mixture.log"))
    env_output_path = resolve_path(os.getenv("PARSE_MIXTURE_ENV", "./out/mixture.env"))

    if not script_path.is_file():
        raise SystemExit(f"Required file not found: {script_path}")
    if not config_path.is_file():
        raise SystemExit(f"Required file not found: {config_path}")

    extra_args = shlex.split(os.getenv("PARSE_MIXTURE_ARGS", ""))
    command = [python_bin, str(script_path), "-c", str(config_path), *extra_args, *sys.argv[1:]]

    print(f"Running mixture parser via {script_path}")
    completed = subprocess.run(
        command,
        cwd=finetune_dir,
        capture_output=True,
        text=True,
    )

    if completed.stdout:
        sys.stdout.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(completed.stdout, encoding="utf-8")

    extracted = parse_assignments(completed.stdout)
    if extracted and env_output_path is not None:
        env_output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(f"{key}={shell_quote(value)}" for key, value in extracted.items()) + "\n"
        env_output_path.write_text(payload, encoding="utf-8")
        print(f"Wrote parsed mixture values to {env_output_path}", file=sys.stderr)

    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
