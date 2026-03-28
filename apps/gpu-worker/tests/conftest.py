from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

import pytest


@pytest.fixture(scope="session")
def worker_module():
    worker_path = Path(__file__).resolve().parents[1] / "worker.py"
    spec = spec_from_file_location("lyrics2song_gpu_worker", worker_path)
    assert spec is not None
    assert spec.loader is not None

    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def make_settings(worker_module, tmp_path):
    def _make_settings(**updates):
        defaults = {
            "redis_url": "redis://127.0.0.1:6379/0",
            "queue_name": "lyrics2song-jobs",
            "database_url": f"sqlite:///{tmp_path / 'worker.db'}",
            "progress_channel": "lyrics2song-job-progress",
            "s3_bucket": "lyrics2song-results",
            "s3_endpoint_url": "https://storage.example.com",
            "s3_access_key_id": "access-key",
            "s3_secret_access_key": "secret-key",
            "s3_session_token": None,
            "s3_region": None,
            "s3_public_base_url": "https://cdn.example.com/results",
            "s3_prefix": "results",
            "s3_force_path_style": True,
            "yue_runner": "cli",
            "yue_python_runner": None,
            "yue_python_executable": "python",
            "yue_workdir": None,
            "yue_infer_script": "infer.py",
            "yue_stage1_model": "stage1-model",
            "yue_stage2_model": "stage2-model",
            "yue_cuda_idx": 0,
            "yue_stage2_batch_size": 4,
            "yue_max_new_tokens": 3000,
            "yue_repetition_penalty": 1.1,
            "yue_segment_seconds": 30,
            "yue_default_prompt_end_time": 30,
            "yue_seed_flag": "--seed",
            "yue_extra_args": [],
            "http_timeout_seconds": 60.0,
        }
        defaults.update(updates)
        return worker_module.Settings(**defaults)

    return _make_settings
