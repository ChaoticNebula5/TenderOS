from tenderos_backend.evals.compliance import ComplianceEvalReport
from tenderos_backend.evals.extraction import ExtractionEvalReport
from tenderos_backend.evals.retrieval import RetrievalEvalReport

MANDATORY_RECALL_GATE = 0.90
CITATION_VALIDITY_GATE = 0.95

# B6's own gate (Build Guide line 3134-3137) - a different pair of numbers
# than B5's, measuring retrieval quality rather than extraction recall, but
# citation correctness happens to share B5's 0.95 threshold by coincidence
# of both being named in their respective "Do not proceed if" sections, not
# because one derives from the other.
RECALL_AT_10_GATE = 0.90
CITATION_CORRECTNESS_GATE = 0.95


def format_extraction_report(report: ExtractionEvalReport) -> str:
    """Console-formatted report for scripts/run_eval.py - states the actual
    numbers, the stop-gate thresholds, and pass/fail explicitly (Build Guide
    Part J: mandatory recall/citation validity are the blocking metrics,
    overall F1 is reported but not blocking), plus enough false-negative and
    citation-failure detail for the "inspect false negatives first" step of
    the failure-action order - a bare pass/fail number with no detail would
    force a second run just to start triaging.
    """
    lines = [
        "=== B5 Extraction Evaluation ===",
        f"Ground truth requirements: {report.total_ground_truth} "
        f"({report.total_mandatory_ground_truth} mandatory)",
        f"Extracted requirements:    {report.total_extracted}",
        "",
        f"Precision:          {report.precision:.3f}",
        f"Recall:             {report.recall:.3f}",
        f"F1:                 {report.f1:.3f}  (reported, not blocking)",
        "",
        f"Mandatory recall:   {report.mandatory_recall:.3f}"
        f"  (gate: >= {MANDATORY_RECALL_GATE})"
        f"  {'PASS' if report.mandatory_recall >= MANDATORY_RECALL_GATE else 'FAIL'}",
        f"Citation validity:  {report.citation_validity:.3f}"
        f"  (gate: >= {CITATION_VALIDITY_GATE})"
        f"  {'PASS' if report.citation_validity >= CITATION_VALIDITY_GATE else 'FAIL'}",
    ]

    gate_passed = (
        report.mandatory_recall >= MANDATORY_RECALL_GATE
        and report.citation_validity >= CITATION_VALIDITY_GATE
    )
    lines.append("")
    lines.append(f"STOP-GATE: {'PASS' if gate_passed else 'FAIL'}")

    if report.false_negatives:
        lines.append("")
        lines.append(f"False negatives ({len(report.false_negatives)} missed requirements):")
        for gt in report.false_negatives:
            marker = "[MANDATORY]" if gt.mandatory else "[optional]"
            lines.append(
                f"  {marker} {gt.requirement_key} - {gt.source_document}"
                f" (page {gt.source_page}) - category {gt.expected_category}"
            )

    if report.citation_failures:
        lines.append("")
        lines.append(f"Citation failures ({len(report.citation_failures)}):")
        for failure in report.citation_failures:
            lines.append(f"  {failure.requirement_key}: {failure.reason}")

    return "\n".join(lines)


def format_retrieval_report(report: RetrievalEvalReport) -> str:
    """Same shape as format_extraction_report above - actual numbers, gate
    thresholds, explicit pass/fail, and enough per-miss detail to start
    triaging without a second run."""
    lines = [
        "=== B6 Retrieval Evaluation ===",
        f"Requirements with evidence: {report.total_requirements_evaluated} evaluated, "
        f"{report.total_requirements_unscoreable} unscoreable (no extracted requirement "
        f"row found for that [REQ-nnn] token)",
        f"Evidence items evaluated:   {report.total_evidence_items}",
        "",
        f"Recall@5:              {report.recall_at_5:.3f}  (reported, not blocking)",
        f"Reranked precision:    {report.reranked_precision:.3f}  (reported, not blocking - "
        "most returned results are legitimately non-evidence for any one query)",
        "",
        f"Recall@10:             {report.recall_at_10:.3f}"
        f"  (gate: >= {RECALL_AT_10_GATE})"
        f"  {'PASS' if report.recall_at_10 >= RECALL_AT_10_GATE else 'FAIL'}",
        f"Citation correctness:  {report.citation_correctness:.3f}"
        f"  (gate: >= {CITATION_CORRECTNESS_GATE})"
        f"  {'PASS' if report.citation_correctness >= CITATION_CORRECTNESS_GATE else 'FAIL'}",
    ]

    gate_passed = (
        report.recall_at_10 >= RECALL_AT_10_GATE
        and report.citation_correctness >= CITATION_CORRECTNESS_GATE
    )
    lines.append("")
    lines.append(f"STOP-GATE: {'PASS' if gate_passed else 'FAIL'}")

    if report.misses:
        lines.append("")
        lines.append(f"Misses ({len(report.misses)}):")
        for miss in report.misses:
            lines.append(f"  {miss.requirement_key} -> {miss.evidence_key} ({miss.evidence_name}): {miss.reason}")

    return "\n".join(lines)


def format_compliance_report(report: ComplianceEvalReport) -> str:
    """B7 has no numeric stop-gate (Build Guide: invariant-based - see the
    three real pass/fail checks the smoke test runs instead) - this report
    is entirely non-blocking, reported for visibility only, same posture
    B5/B6's own non-blocking metrics (F1, Recall@5, reranked precision)
    already carry alongside their real gates.
    """
    lines = [
        "=== B7 Compliance Evaluation (reported, not blocking) ===",
        f"Scored: {report.total_scored}  "
        f"(excluded as needs_review: {report.total_excluded_needs_review}, "
        f"unscoreable: {report.total_unscoreable})",
        "",
        f"Macro-F1:                    {report.macro_f1:.3f}",
        f"Unsupported-compliance rate: {report.unsupported_compliance_rate:.3f}"
        "  (fraction of expected-evidence_unavailable rows scored as "
        "fully/partially compliant - should be 0.0)",
    ]

    if report.confusion_matrix:
        lines.append("")
        lines.append("Confusion matrix (expected -> actual: count):")
        for (expected, actual), count in sorted(report.confusion_matrix.items()):
            marker = "" if expected == actual else "  [MISMATCH]"
            lines.append(f"  {expected} -> {actual}: {count}{marker}")

    if report.mismatches:
        lines.append("")
        lines.append(f"Mismatches ({len(report.mismatches)}):")
        for m in report.mismatches:
            lines.append(f"  {m.requirement_key}: expected {m.expected_status}, got {m.actual_status}")

    return "\n".join(lines)
