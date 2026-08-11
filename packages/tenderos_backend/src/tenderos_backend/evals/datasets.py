import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GroundTruthRequirement:
    """Mirrors Part L2's JSONL schema exactly - one row per planted
    requirement in a synthetic (or, later, redacted-real) ground-truth
    package."""

    requirement_key: str
    mandatory: bool
    source_document: str
    source_page: int | None
    expected_category: str
    expected_compliance: str
    valid_evidence_keys: list[str]


def load_requirements_ground_truth(path: Path) -> list[GroundTruthRequirement]:
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            records.append(
                GroundTruthRequirement(
                    requirement_key=data["requirement_key"],
                    mandatory=data["mandatory"],
                    source_document=data["source_document"],
                    source_page=data.get("source_page"),
                    expected_category=data["expected_category"],
                    expected_compliance=data["expected_compliance"],
                    valid_evidence_keys=data.get("valid_evidence_keys", []),
                )
            )
    return records
