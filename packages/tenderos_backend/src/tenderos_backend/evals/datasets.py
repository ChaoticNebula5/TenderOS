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


def load_evidence_key_names(path: Path) -> dict[str, str]:
    """tender_a_knowledge.jsonl (B5's own extension of L2, not a locked
    schema) carries certification/project/capability records keyed the same
    way tender_a_requirements.jsonl's valid_evidence_keys references them
    (e.g. "CERT-01", "PROJECT-03") - this resolves key -> the record's own
    `name`, which retrieval eval then resolves to a real knowledge_item_id
    by title lookup (evals/retrieval.py), since the evidence key itself is
    never persisted anywhere in the database.
    """
    names: dict[str, str] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if (
                data.get("record_type") in ("certification", "project", "capability")
                and "name" in data
            ):
                names[data["key"]] = data["name"]
    return names


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
