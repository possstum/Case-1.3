import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


REQUIRED_FIELDS = (
    "codec",
    "vocals_codec",
    "instrumental_codec",
    "audio_length_in_sec",
    "msa",
    "genres",
    "splitted_lyrics.segmented_lyrics",
)
NPY_FIELDS = ("codec", "vocals_codec", "instrumental_codec")
TIMING_ROOTS = ("msa",)
FRAME_ROOT = "splitted_lyrics.segmented_lyrics"
EPSILON = 1e-6


@dataclass
class ValidationIssue:
    code: str
    path: str
    message: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate YuE JSONL metadata and emit a JSON report."
    )
    parser.add_argument("dataset", type=Path, help="Path to the YuE JSONL dataset.")
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=None,
        help="Base directory for relative codec .npy paths. Defaults to the dataset directory.",
    )
    parser.add_argument(
        "--report-out",
        type=Path,
        default=None,
        help="Where to write the JSON report. Defaults to <dataset>.validation_report.json.",
    )
    return parser.parse_args()


def default_report_path(dataset: Path) -> Path:
    return dataset.with_name(f"{dataset.stem}.validation_report.json")


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def get_nested_value(payload: dict[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def resolve_path(raw_path: str, base_dir: Path) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve(strict=False)


def add_issue(
    issues: list[ValidationIssue], code: str, path: str, message: str
) -> None:
    issues.append(ValidationIssue(code=code, path=path, message=message))


def validate_required_fields(payload: dict[str, Any], issues: list[ValidationIssue]) -> None:
    for field in REQUIRED_FIELDS:
        value = get_nested_value(payload, field)
        if is_missing(value):
            add_issue(
                issues,
                "missing_required_field",
                field,
                f"Missing or empty required field '{field}'.",
            )


def validate_audio_length(
    payload: dict[str, Any], issues: list[ValidationIssue]
) -> Optional[float]:
    value = payload.get("audio_length_in_sec")
    try:
        audio_length = float(value)
    except (TypeError, ValueError):
        add_issue(
            issues,
            "invalid_audio_length",
            "audio_length_in_sec",
            "audio_length_in_sec must be a number.",
        )
        return None

    if audio_length <= 0:
        add_issue(
            issues,
            "invalid_audio_length",
            "audio_length_in_sec",
            "audio_length_in_sec must be greater than 0.",
        )
        return None

    return audio_length


def validate_npy_files(
    payload: dict[str, Any], base_dir: Path, issues: list[ValidationIssue]
) -> dict[str, str]:
    resolved_paths: dict[str, str] = {}
    for field in NPY_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            continue

        resolved = resolve_path(value, base_dir)
        resolved_paths[field] = str(resolved)

        if resolved.suffix.lower() != ".npy":
            add_issue(
                issues,
                "invalid_npy_path",
                field,
                f"{field} must point to a .npy file: {value}",
            )
        if not resolved.is_file():
            add_issue(
                issues,
                "missing_npy_file",
                field,
                f"Referenced file does not exist: {resolved}",
            )
    return resolved_paths


def validate_scalar_timing(
    container: dict[str, Any],
    path: str,
    audio_length: float,
    issues: list[ValidationIssue],
) -> None:
    has_offset = "offset" in container
    has_duration = "duration" in container
    if not has_offset and not has_duration:
        return
    if not has_offset or not has_duration:
        add_issue(
            issues,
            "invalid_timing",
            path,
            "Timing object must contain both 'offset' and 'duration'.",
        )
        return

    try:
        offset = float(container["offset"])
        duration = float(container["duration"])
    except (TypeError, ValueError):
        add_issue(
            issues,
            "invalid_timing",
            path,
            "offset and duration must be numeric values.",
        )
        return

    if offset < 0:
        add_issue(issues, "invalid_timing", f"{path}.offset", "offset must be >= 0.")
    if duration < 0:
        add_issue(
            issues,
            "invalid_timing",
            f"{path}.duration",
            "duration must be >= 0.",
        )
    if offset - EPSILON > audio_length:
        add_issue(
            issues,
            "timing_out_of_bounds",
            f"{path}.offset",
            f"offset={offset} exceeds audio_length_in_sec={audio_length}.",
        )
    if offset + duration - audio_length > EPSILON:
        add_issue(
            issues,
            "timing_out_of_bounds",
            path,
            f"offset + duration = {offset + duration} exceeds audio_length_in_sec={audio_length}.",
        )


def validate_array_timing(
    container: dict[str, Any],
    path: str,
    audio_length: float,
    issues: list[ValidationIssue],
) -> None:
    has_offsets = "offsets" in container
    has_durations = "durations" in container
    if not has_offsets and not has_durations:
        return
    if not has_offsets or not has_durations:
        add_issue(
            issues,
            "invalid_timing",
            path,
            "Timing object must contain both 'offsets' and 'durations'.",
        )
        return

    offsets = container["offsets"]
    durations = container["durations"]
    if not isinstance(offsets, list) or not isinstance(durations, list):
        add_issue(
            issues,
            "invalid_timing",
            path,
            "'offsets' and 'durations' must be arrays.",
        )
        return

    if len(offsets) != len(durations):
        add_issue(
            issues,
            "invalid_timing",
            path,
            "'offsets' and 'durations' must have the same length.",
        )
        return

    for index, (offset_value, duration_value) in enumerate(zip(offsets, durations)):
        item_path = f"{path}[{index}]"
        try:
            offset = float(offset_value)
            duration = float(duration_value)
        except (TypeError, ValueError):
            add_issue(
                issues,
                "invalid_timing",
                item_path,
                "offset and duration must be numeric values.",
            )
            continue

        if offset < 0:
            add_issue(
                issues,
                "invalid_timing",
                f"{item_path}.offset",
                "offset must be >= 0.",
            )
        if duration < 0:
            add_issue(
                issues,
                "invalid_timing",
                f"{item_path}.duration",
                "duration must be >= 0.",
            )
        if offset - EPSILON > audio_length:
            add_issue(
                issues,
                "timing_out_of_bounds",
                f"{item_path}.offset",
                f"offset={offset} exceeds audio_length_in_sec={audio_length}.",
            )
        if offset + duration - audio_length > EPSILON:
            add_issue(
                issues,
                "timing_out_of_bounds",
                item_path,
                f"offset + duration = {offset + duration} exceeds audio_length_in_sec={audio_length}.",
            )


def validate_codec_frame_range(
    container: dict[str, Any], path: str, issues: list[ValidationIssue]
) -> None:
    has_start = "codec_frame_start" in container
    has_end = "codec_frame_end" in container
    if not has_start and not has_end:
        return
    if not has_start or not has_end:
        add_issue(
            issues,
            "invalid_codec_frame_range",
            path,
            "Segment must contain both codec_frame_start and codec_frame_end.",
        )
        return

    start = container["codec_frame_start"]
    end = container["codec_frame_end"]
    if not isinstance(start, int) or isinstance(start, bool):
        add_issue(
            issues,
            "invalid_codec_frame_range",
            f"{path}.codec_frame_start",
            "codec_frame_start must be an integer.",
        )
        return
    if not isinstance(end, int) or isinstance(end, bool):
        add_issue(
            issues,
            "invalid_codec_frame_range",
            f"{path}.codec_frame_end",
            "codec_frame_end must be an integer.",
        )
        return
    if start < 0:
        add_issue(
            issues,
            "invalid_codec_frame_range",
            f"{path}.codec_frame_start",
            "codec_frame_start must be >= 0.",
        )
    if end <= start:
        add_issue(
            issues,
            "invalid_codec_frame_range",
            path,
            f"codec_frame_start ({start}) must be less than codec_frame_end ({end}).",
        )


def walk_timing_objects(
    value: Any, path: str, audio_length: float, issues: list[ValidationIssue]
) -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            walk_timing_objects(item, f"{path}[{index}]", audio_length, issues)
        return

    if not isinstance(value, dict):
        return

    validate_scalar_timing(value, path, audio_length, issues)
    validate_array_timing(value, path, audio_length, issues)

    for key, nested in value.items():
        walk_timing_objects(nested, f"{path}.{key}", audio_length, issues)


def validate_segmented_lyrics(
    value: Any, path: str, audio_length: float, issues: list[ValidationIssue]
) -> None:
    if not isinstance(value, list):
        add_issue(
            issues,
            "invalid_segmented_lyrics",
            path,
            "splitted_lyrics.segmented_lyrics must be an array.",
        )
        return

    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            add_issue(
                issues,
                "invalid_segmented_lyrics",
                item_path,
                "Segment entry must be an object.",
            )
            continue

        validate_codec_frame_range(item, item_path, issues)
        walk_timing_objects(item, item_path, audio_length, issues)


def validate_entry(
    payload: dict[str, Any], base_dir: Path
) -> tuple[list[ValidationIssue], dict[str, str]]:
    issues: list[ValidationIssue] = []
    validate_required_fields(payload, issues)

    audio_length = validate_audio_length(payload, issues)
    resolved_paths = validate_npy_files(payload, base_dir, issues)

    if audio_length is None:
        return issues, resolved_paths

    for root in TIMING_ROOTS:
        value = get_nested_value(payload, root)
        if value is None:
            continue
        walk_timing_objects(value, root, audio_length, issues)

    segmented_lyrics = get_nested_value(payload, FRAME_ROOT)
    if segmented_lyrics is not None:
        validate_segmented_lyrics(segmented_lyrics, FRAME_ROOT, audio_length, issues)

    return issues, resolved_paths


def build_report(
    dataset: Path,
    base_dir: Path,
    entries: list[dict[str, Any]],
    empty_lines: int,
    json_errors: int,
    io_error: Optional[str] = None,
) -> dict[str, Any]:
    issue_counter: Counter[str] = Counter()
    valid_entries = 0
    invalid_entries = 0

    for entry in entries:
        issues = entry["issues"]
        if issues:
            invalid_entries += 1
            issue_counter.update(issue["code"] for issue in issues)
        else:
            valid_entries += 1

    summary = {
        "total_lines": len(entries) + empty_lines,
        "checked_entries": len(entries),
        "valid_entries": valid_entries,
        "invalid_entries": invalid_entries,
        "empty_lines": empty_lines,
        "json_errors": json_errors,
        "total_issues": sum(issue_counter.values()),
        "issues_by_code": dict(issue_counter),
    }

    report: dict[str, Any] = {
        "dataset": str(dataset.resolve(strict=False)),
        "base_dir": str(base_dir.resolve(strict=False)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "entries": entries,
    }
    if io_error is not None:
        report["io_error"] = io_error
    return report


def print_summary(report: dict[str, Any], report_out: Path) -> None:
    summary = report["summary"]
    print(f"Dataset: {report['dataset']}")
    print(f"Report: {report_out.resolve(strict=False)}")
    print(f"Checked entries: {summary['checked_entries']}")
    print(f"Valid entries: {summary['valid_entries']}")
    print(f"Invalid entries: {summary['invalid_entries']}")
    print(f"Empty lines: {summary['empty_lines']}")
    print(f"JSON errors: {summary['json_errors']}")
    print(f"Total issues: {summary['total_issues']}")

    issues_by_code = summary["issues_by_code"]
    if issues_by_code:
        print("Issues by code:")
        for code in sorted(issues_by_code):
            print(f"- {code}: {issues_by_code[code]}")


def write_report(report_out: Path, report: dict[str, Any]) -> None:
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    dataset = args.dataset
    base_dir = args.base_dir or dataset.parent
    report_out = args.report_out or default_report_path(dataset)

    if not dataset.is_file():
        report = build_report(
            dataset=dataset,
            base_dir=base_dir,
            entries=[],
            empty_lines=0,
            json_errors=0,
            io_error=f"Dataset file does not exist: {dataset}",
        )
        write_report(report_out, report)
        print_summary(report, report_out)
        print(f"Fatal error: {report['io_error']}")
        return 2

    entries: list[dict[str, Any]] = []
    empty_lines = 0
    json_errors = 0

    with dataset.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                empty_lines += 1
                continue

            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                json_errors += 1
                entries.append(
                    {
                        "line": line_no,
                        "status": "invalid_json",
                        "issues": [
                            asdict(
                                ValidationIssue(
                                    code="invalid_json",
                                    path="$",
                                    message=f"Invalid JSON: {exc.msg}",
                                )
                            )
                        ],
                    }
                )
                continue

            if not isinstance(payload, dict):
                entries.append(
                    {
                        "line": line_no,
                        "status": "invalid",
                        "issues": [
                            asdict(
                                ValidationIssue(
                                    code="invalid_row_type",
                                    path="$",
                                    message="Row must be a JSON object.",
                                )
                            )
                        ],
                    }
                )
                continue

            issues, resolved_paths = validate_entry(payload, base_dir)
            entries.append(
                {
                    "line": line_no,
                    "status": "valid" if not issues else "invalid",
                    "issues": [asdict(issue) for issue in issues],
                    "resolved_paths": resolved_paths,
                }
            )

    report = build_report(
        dataset=dataset,
        base_dir=base_dir,
        entries=entries,
        empty_lines=empty_lines,
        json_errors=json_errors,
    )
    write_report(report_out, report)
    print_summary(report, report_out)

    return 0 if report["summary"]["invalid_entries"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
