"""Run the synthetic classification evaluation against the configured model."""

import argparse
import json
import os
from pathlib import Path
import re

from backend.main import load_local_settings

load_local_settings()

from backend.runtime import CLASSIFICATION_BATCH_SIZE, classify_batch


EVAL_DIR = Path(__file__).resolve().parent
DATASET_PATH = EVAL_DIR / "dataset.json"


def run(prompt_version: str) -> tuple[float, list[dict[str, str]], int]:
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    correct = 0
    misses = []
    total = len(dataset)

    for offset in range(0, total, CLASSIFICATION_BATCH_SIZE):
        batch = dataset[offset : offset + CLASSIFICATION_BATCH_SIZE]
        try:
            predictions = classify_batch(batch, prompt_version=prompt_version)
        except Exception as exc:
            predictions = {}
            error = f"{type(exc).__name__}: {exc}"
        else:
            error = ""

        for email in batch:
            predicted = predictions.get(email["id"])
            if predicted == email["expected"]:
                correct += 1
            else:
                miss = {
                    "id": email["id"],
                    "expected": email["expected"],
                    "predicted": predicted or "(no prediction)",
                }
                if error:
                    miss["error"] = error
                misses.append(miss)

    accuracy = correct / total if total else 0.0
    return accuracy, misses, total


def record_result(prompt_version: str, accuracy: float, total: int) -> None:
    results_path = EVAL_DIR / "RESULTS.md"
    content = results_path.read_text(encoding="utf-8")
    correct = round(accuracy * total)
    row = f"| {prompt_version.title()} | {correct} | {total} | {accuracy:.1%} |"
    updated, count = re.subn(
        rf"^\| {prompt_version.title()} \|.*$",
        row,
        content,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise RuntimeError(f"Could not find the {prompt_version} row in RESULTS.md.")
    results_path.write_text(updated, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prompt-version",
        choices=("baseline", "current"),
        default=None,
        help="Use the original prompt or the updated prompt.",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Run both prompt versions and write both scores to evals/RESULTS.md.",
    )
    args = parser.parse_args()
    if not os.getenv("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY is missing. Set it locally before running the model evaluation.")
    versions = ("baseline", "current") if args.compare else (args.prompt_version or "current",)
    for prompt_version in versions:
        accuracy, misses, total = run(prompt_version)
        print(f"Prompt: {prompt_version}")
        print(f"Accuracy: {accuracy:.1%} ({total - len(misses)}/{total})")
        print("Misses:")
        if not misses:
            print("  None")
        for miss in misses:
            details = (
                f"expected={miss['expected']}; predicted={miss['predicted']}"
            )
            if miss.get("error"):
                details += f"; error={miss['error']}"
            print(f"  {miss['id']}: {details}")
        record_result(prompt_version, accuracy, total)


if __name__ == "__main__":
    main()
