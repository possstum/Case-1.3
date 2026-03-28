from pathlib import Path
import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cli
import validate_yue_jsonl as validator


def test_cli_main_passes_for_valid_dataset(monkeypatch, tmp_path, capsys) -> None:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "artist": "Motorama",
                "title": "Alps",
                "lyrics": "We walked into the night",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["cli.py", str(dataset)])

    exit_code = cli.main()

    assert exit_code == 0
    assert "Dataset validation passed." in capsys.readouterr().out


def test_cli_main_reports_invalid_rows(monkeypatch, tmp_path, capsys) -> None:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        '{"artist":"Motorama","title":"","lyrics":"text"}\n'
        '{"artist":"Motorama","title":"Alps","lyrics":"text"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["cli.py", str(dataset)])

    exit_code = cli.main()
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "missing or empty 'title'" in output
    assert "invalid JSON" in output


def test_validate_entry_reports_missing_files_and_invalid_timing(tmp_path: Path) -> None:
    payload = {
        "codec": "codec.npy",
        "vocals_codec": "vocals.npy",
        "instrumental_codec": "instrumental.npy",
        "audio_length_in_sec": 12,
        "msa": {
            "offset": 11,
            "duration": 3,
        },
        "genres": ["dream-pop"],
        "splitted_lyrics": {
            "segmented_lyrics": [
                {
                    "codec_frame_start": 10,
                    "codec_frame_end": 5,
                }
            ]
        },
    }

    issues, resolved_paths = validator.validate_entry(payload, tmp_path)

    issue_codes = {issue.code for issue in issues}
    assert "missing_npy_file" in issue_codes
    assert "timing_out_of_bounds" in issue_codes
    assert "invalid_codec_frame_range" in issue_codes
    assert resolved_paths["codec"].endswith("codec.npy")


def test_validate_yue_jsonl_main_writes_report(monkeypatch, tmp_path, capsys) -> None:
    dataset = tmp_path / "yue.jsonl"
    report_out = tmp_path / "report.json"
    for filename in ("codec.npy", "vocals.npy", "instrumental.npy"):
        (tmp_path / filename).write_bytes(b"npy")
    dataset.write_text(
        json.dumps(
            {
                "codec": "codec.npy",
                "vocals_codec": "vocals.npy",
                "instrumental_codec": "instrumental.npy",
                "audio_length_in_sec": 10,
                "msa": {"offset": 0, "duration": 10},
                "genres": ["dream-pop"],
                "splitted_lyrics": {
                    "segmented_lyrics": [
                        {
                            "codec_frame_start": 0,
                            "codec_frame_end": 1,
                            "offset": 0,
                            "duration": 5,
                        }
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_yue_jsonl.py",
            str(dataset),
            "--report-out",
            str(report_out),
        ],
    )

    exit_code = validator.main()
    output = capsys.readouterr().out
    report = json.loads(report_out.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert "Valid entries: 1" in output
    assert report["summary"]["valid_entries"] == 1
    assert report["summary"]["invalid_entries"] == 0
