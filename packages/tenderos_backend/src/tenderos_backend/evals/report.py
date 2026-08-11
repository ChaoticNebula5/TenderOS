from tenderos_backend.evals.extraction import ExtractionEvalReport

MANDATORY_RECALL_GATE = 0.90
CITATION_VALIDITY_GATE = 0.95


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
