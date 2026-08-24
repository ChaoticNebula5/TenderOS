import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  ApiError,
  generateProposal,
  listProposalClaims,
  listProposalSections,
  patchProposalSection,
} from "../api/client";
import type { ProposalClaimResponse, ProposalSectionResponse } from "../api/client";
import { TraceabilityDrawer } from "../components/TraceabilityDrawer";

// Screen 13 (Part J). One row per drafted section, each with its own
// claim list (listProposalClaims - a B13 addition, KNOWN_LIMITATIONS.md:
// no route ever exposed this before this block) so a human can click a
// claim into screen 16's drawer, rendered inline below via
// selectedClaimId rather than as its own route.
export function ProposalWorkspace() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const [sections, setSections] = useState<ProposalSectionResponse[]>([]);
  const [claimsBySection, setClaimsBySection] = useState<Record<string, ProposalClaimResponse[]>>(
    {},
  );
  const [editingContent, setEditingContent] = useState<Record<string, string>>({});
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);

  async function refreshSections() {
    if (!tenderId) return;
    const result = await listProposalSections(tenderId);
    setSections(result);
    for (const section of result) {
      const claims = await listProposalClaims(section.id);
      setClaimsBySection((prev) => ({ ...prev, [section.id]: claims }));
    }
  }

  useEffect(() => {
    refreshSections().catch(() => setError("Failed to load proposal sections"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenderId]);

  async function handleGenerate() {
    if (!tenderId) return;
    setError(null);
    setGenerating(true);
    try {
      await generateProposal(tenderId, crypto.randomUUID());
      await refreshSections();
    } catch (err) {
      setError(err instanceof ApiError ? `Generate failed (${err.status})` : "Generate failed");
    } finally {
      setGenerating(false);
    }
  }

  async function handleSaveContent(sectionId: string) {
    setError(null);
    try {
      const content = editingContent[sectionId];
      const updated = await patchProposalSection(sectionId, content);
      setSections((prev) => prev.map((s) => (s.id === sectionId ? updated : s)));
      setEditingContent((prev) => {
        const next = { ...prev };
        delete next[sectionId];
        return next;
      });
    } catch (err) {
      setError(err instanceof ApiError ? `Save failed (${err.status})` : "Save failed");
    }
  }

  return (
    <main>
      <h1>Proposal workspace</h1>

      {error && (
        <p role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}

      <button type="button" onClick={handleGenerate} disabled={generating}>
        {generating ? "Generating..." : "Generate proposal"}
      </button>

      {sections.length === 0 && <p>No proposal sections drafted yet.</p>}

      {sections.map((section) => (
        <section key={section.id} style={{ marginTop: "var(--space-5)" }}>
          <h2>
            {section.section_name} (v{section.version}, {section.status})
          </h2>
          <textarea
            rows={6}
            style={{ width: "100%" }}
            value={editingContent[section.id] ?? section.content}
            onChange={(e) =>
              setEditingContent((prev) => ({ ...prev, [section.id]: e.target.value }))
            }
          />
          <button
            type="button"
            onClick={() => handleSaveContent(section.id)}
            disabled={editingContent[section.id] === undefined}
          >
            Save edits
          </button>

          <h3>Claims</h3>
          <ul>
            {(claimsBySection[section.id] ?? []).map((claim) => (
              <li key={claim.id}>
                [{claim.claim_type} / {claim.grounding_status}] {claim.claim_text}{" "}
                <button type="button" onClick={() => setSelectedClaimId(claim.id)}>
                  Inspect trace
                </button>
              </li>
            ))}
            {(claimsBySection[section.id] ?? []).length === 0 && (
              <li>No claims in this section.</li>
            )}
          </ul>
        </section>
      ))}

      {selectedClaimId && (
        <TraceabilityDrawer claimId={selectedClaimId} onClose={() => setSelectedClaimId(null)} />
      )}
    </main>
  );
}
