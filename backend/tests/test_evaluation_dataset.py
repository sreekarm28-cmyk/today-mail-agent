"""Sanity checks for the synthetic model-evaluation set."""

import json
from collections import Counter
from pathlib import Path

from backend.runtime import CLASSIFICATION_CATEGORIES


def test_evaluation_dataset_has_balanced_categories_and_injections():
    dataset_path = Path(__file__).resolve().parents[2] / "evals" / "dataset.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    counts = Counter(email["expected"] for email in dataset)

    assert 40 <= len(dataset) <= 50
    assert set(counts) == set(CLASSIFICATION_CATEGORIES)
    assert all(8 <= count <= 10 for count in counts.values())
    injection_examples = [
        email for email in dataset
        if "assistant" in email["body"].lower()
        and any(word in email["body"].lower() for word in ("ignore", "reveal", "classify"))
    ]
    assert len(injection_examples) == 3
