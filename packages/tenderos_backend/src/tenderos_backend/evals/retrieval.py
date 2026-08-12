import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from tenderos_backend.agents.knowledge.retrieval import search_company_knowledge
from tenderos_backend.db.models.requirement import Requirement
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.evals.datasets import GroundTruthRequirement


@dataclass(frozen=True)
class EvidenceMiss:
    requirement_key: str
    evidence_key: str
    evidence_name: str
    reason: str


@dataclass(frozen=True)
class RetrievalEvalReport:
    recall_at_5: float
    recall_at_10: float
    reranked_precision: float
    citation_correctness: float
    total_evidence_items: int
    total_requirements_evaluated: int
    total_requirements_unscoreable: int
    misses: list[EvidenceMiss] = field(default_factory=list)


async def evaluate_retrieval(
    ground_truth: list[GroundTruthRequirement],
    evidence_names: dict[str, str],
    organization_id: uuid.UUID,
    tender_id: uuid.UUID,
    retrieval_top_k: int,
    rerank_top_n: int,
) -> RetrievalEvalReport:
    """Recall@K: of every (requirement, expected evidence item) pair in the
    ground truth, was that evidence item's knowledge_item returned within
    the top K search_company_knowledge results when querying with the
    requirement's own text. Matched by knowledge_item_id (resolved from the
    evidence key's name via a title lookup), never by comparing title
    strings directly - an exact-identity join, not a fuzzy one.

    citation_correctness is deliberately independent of recall, same
    separation B5's evaluate_extraction already established: it does not
    ask whether the RIGHT item was found, only whether every RETURNED
    result's own citation is internally consistent (the chunk it points to
    actually belongs to the document it claims, and the knowledge_item it
    names actually has that chunk as its own source_chunk_id) - a retrieval
    bug that returns a correct knowledge_item_id paired with a mismatched
    excerpt would show up here, not in recall.

    A requirement whose planted [REQ-nnn] token isn't found in ANY
    persisted Requirement row FOR THIS TENDER (e.g. B5's extraction was
    never run for this eval run) is unscoreable, not a miss - excluded from
    the denominator entirely rather than counted as 0% recall for evidence
    that was never even queried, tracked separately via
    total_requirements_unscoreable so that distinction stays visible in the
    report rather than silently inflating or deflating recall. tender_id is
    required, not inferred, precisely because the requirements table
    accumulates rows across every past eval run in this database - an
    unscoped text search would risk matching a stale row from a different
    run (caught live: an earlier version of this function had exactly that
    bug, via .limit(1) with no tender filter).
    """
    evaluated = [gt for gt in ground_truth if gt.valid_evidence_keys]

    recall_hits_5 = 0
    recall_hits_10 = 0
    total_evidence_items = 0
    misses: list[EvidenceMiss] = []
    unscoreable = 0

    precision_hits = 0
    precision_total = 0

    citation_checked = 0
    citation_correct = 0

    async with UnitOfWork() as uow:
        all_items = await uow.knowledge.list_knowledge_items_for_org(organization_id)
        name_to_id = {item.title: item.id for item in all_items}
        evidence_key_to_id = {
            key: name_to_id[name] for key, name in evidence_names.items() if name in name_to_id
        }

        for gt in evaluated:
            token = f"[{gt.requirement_key}]"
            result = await uow.session.execute(
                select(Requirement)
                .where(
                    Requirement.requirement_text.contains(token),
                    Requirement.tender_id == tender_id,
                )
                .limit(1)
            )
            requirement = result.scalar_one_or_none()
            if requirement is None:
                unscoreable += 1
                continue

            results = await search_company_knowledge(
                organization_id,
                requirement.requirement_text,
                retrieval_top_k=retrieval_top_k,
                rerank_top_n=rerank_top_n,
            )
            ids_ranked = [r.knowledge_item_id for r in results]
            expected_ids = {
                evidence_key_to_id[key]
                for key in gt.valid_evidence_keys
                if key in evidence_key_to_id
            }

            precision_total += len(ids_ranked)
            precision_hits += sum(1 for item_id in ids_ranked if item_id in expected_ids)

            for evidence_key in gt.valid_evidence_keys:
                total_evidence_items += 1
                expected_name = evidence_names.get(evidence_key, "<unknown key>")
                expected_id = evidence_key_to_id.get(evidence_key)
                if expected_id is None:
                    misses.append(
                        EvidenceMiss(
                            gt.requirement_key,
                            evidence_key,
                            expected_name,
                            "evidence item not found in knowledge_items (never persisted, or "
                            "title mismatch against tender_a_knowledge.jsonl)",
                        )
                    )
                    continue
                rank = ids_ranked.index(expected_id) + 1 if expected_id in ids_ranked else None
                if rank is not None and rank <= 5:
                    recall_hits_5 += 1
                if rank is not None and rank <= 10:
                    recall_hits_10 += 1
                if rank is None:
                    misses.append(
                        EvidenceMiss(
                            gt.requirement_key,
                            evidence_key,
                            expected_name,
                            f"not returned in top {retrieval_top_k}",
                        )
                    )

            for r in results:
                citation_checked += 1
                chunk = await uow.documents.get_chunk(r.chunk_id)
                if chunk is None or chunk.document_id != r.document_id:
                    continue
                knowledge_item = await uow.knowledge.get_knowledge_item(r.knowledge_item_id)
                if knowledge_item is None or knowledge_item.source_chunk_id != r.chunk_id:
                    continue
                citation_correct += 1

    return RetrievalEvalReport(
        recall_at_5=recall_hits_5 / total_evidence_items if total_evidence_items else 1.0,
        recall_at_10=recall_hits_10 / total_evidence_items if total_evidence_items else 1.0,
        reranked_precision=precision_hits / precision_total if precision_total else 1.0,
        citation_correctness=citation_correct / citation_checked if citation_checked else 1.0,
        total_evidence_items=total_evidence_items,
        total_requirements_evaluated=len(evaluated) - unscoreable,
        total_requirements_unscoreable=unscoreable,
        misses=misses,
    )
