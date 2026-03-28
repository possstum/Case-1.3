import argparse
import json
from pathlib import Path


REQUIRED_FIELDS = ("artist", "title", "lyrics")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate lyrics2song dataset JSONL files."
    )
    parser.add_argument("dataset", type=Path, help="Path to a JSONL dataset file.")
    return parser.parse_args()


def validate_row(row: dict, line_no: int) -> list[str]:
    errors: list[str] = []
    for field in REQUIRED_FIELDS:
        if field not in row or not str(row[field]).strip():
            errors.append(f"line {line_no}: missing or empty '{field}'")
    return errors


def main() -> int:
    args = parse_args()
    errors: list[str] = []

    with args.dataset.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {index}: invalid JSON ({exc.msg})")
                continue
            if not isinstance(payload, dict):
                errors.append(f"line {index}: row must be a JSON object")
                continue
            errors.extend(validate_row(payload, index))

    if errors:
        print("Dataset validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Dataset validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

