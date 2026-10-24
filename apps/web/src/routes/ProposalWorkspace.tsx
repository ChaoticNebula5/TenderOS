import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import {
  ApiError,
  generateProposal,
  listProposalClaims,
  listProposalSections,
  patchProposalSection,
} from "../api/client";
import type {
  OperationResponse,
  ProposalClaimResponse,
  ProposalSectionResponse,
} from "../api/client";
import { TraceabilityDrawer } from "../components/TraceabilityDrawer";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { Field } from "../components/ui/Field";
import { Notice } from "../components/ui/Notice";
import { StatusBadge } from "../components/ui/StatusBadge";
import { humanize } from "../format/humanize";
import { useListReads } from "../hooks/useListReads";
import { useTenderOperation } from "../hooks/useTenderOperation";
import { describeStatus, describeStatusKeepingRawWord } from "../status/statusMap";
import "./ProposalWorkspace.css";

// Screen 13 (Part J). One row per drafted section, each with its own
// claim list (listProposalClaims - a B13 addition, KNOWN_LIMITATIONS.md:
// no route ever exposed this before this block) so a human can click a
// claim into screen 16's drawer, rendered inline below via
// selectedClaimId rather than as its own route.
//
// E2E contract (full-flow.spec.ts): every proposal section is a <section>
// that is a DIRECT child of <main> (:952 counts `main > section`), so the
// empty state is not a <section> and nothing wraps the sections; each
// claim is an <li> whose text holds "[type / grounding]" (:971, :974 find
// unsupported claims by "/ unsupported]"), with its "Inspect trace" button
// inside it (:972).
export function ProposalWorkspace() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const [sections, setSections] = useState<ProposalSectionResponse[]>([]);
  // Set once listProposalSections answers, so the empty state only claims
  // "nothing drafted" after the response says so (DESIGN.md Copy rules).
  const [sectionsLoaded, setSectionsLoaded] = useState(false);
  // A saved edit can land while the re-read after a generation is in
  // flight; that read's answer must not undo it (useListReads.ts).
  const sectionReads = useListReads();
  const [claimsBySection, setClaimsBySection] = useState<Record<string, ProposalClaimResponse[]>>(
    {},
  );
  const [editingContent, setEditingContent] = useState<Record<string, string>>({});
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The "Inspect trace" button that opened the drawer, so focus can return
  // to it on close (DESIGN.md Accessibility, "Keyboard order").
  const drawerOpener = useRef<HTMLElement | null>(null);
  // The tender on screen now, kept fresh on every render, so a read that
  // answers after the user moved to another tender can tell (the same guard
  // as CommercialFindings).
  const tenderIdRef = useRef(tenderId);
  tenderIdRef.current = tenderId;

  // isCancelled: true once this read's result no longer belongs on screen
  // (the user moved to another tender); checked after every await, so a late
  // answer is dropped instead of painting the previous tender's sections.
  async function refreshSections(isCancelled: () => boolean) {
    if (!tenderId) return;
    const startedAt = sectionReads.start();
    const result = await listProposalSections(tenderId);
    if (isCancelled()) return;
    setSections((current) => sectionReads.merge(startedAt, result, current));
    setSectionsLoaded(true);
    for (const section of result) {
      const claims = await listProposalClaims(section.id);
      if (isCancelled()) return;
      setClaimsBySection((prev) => ({ ...prev, [section.id]: claims }));
    }
  }

  useEffect(() => {
    let cancelled = false;
    setSectionsLoaded(false);
    refreshSections(() => cancelled).catch(() => {
      if (!cancelled) setError("Failed to load proposal sections");
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenderId]);

  // Phase 4 Stage 2: the POST returns 202 and the worker drafts. Generate
  // stays busy until the operation is finished AND the sections (with their
  // claims) have been re-read: full-flow.spec.ts:949-953 waits for the button
  // to be enabled, then expects a section visible, and step 10 (:974)
  // counts claim rows at once, without waiting. A generation started before a reload is
  // picked up again on load (useTenderOperation).
  async function handleFinished(operation: OperationResponse) {
    const finishedForTenderId = tenderId;
    const isCancelled = () => tenderIdRef.current !== finishedForTenderId;
    if (operation.status === "failed") {
      setError(operation.error_message ?? "Generate failed");
    }
    await refreshSections(isCancelled).catch(() => {
      if (!isCancelled()) setError("Failed to load proposal sections");
    });
  }
  const { busy: generating, start } = useTenderOperation(
    tenderId,
    "proposal_generation",
    handleFinished,
  );

  async function handleGenerate() {
    if (!tenderId) return;
    setError(null);
    try {
      // One key per click: a network retry of this request replays its 202.
      await start(() => generateProposal(tenderId, crypto.randomUUID()));
    } catch (err) {
      setError(err instanceof ApiError ? `Generate failed (${err.status})` : "Generate failed");
    }
  }

  async function handleSaveContent(sectionId: string) {
    setError(null);
    try {
      const content = editingContent[sectionId];
      const updated = await patchProposalSection(sectionId, content);
      sectionReads.changed(sectionId);
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
        <Notice variant="failed" alert>
          {error}
        </Notice>
      )}

      <div className="action-row proposal__actions">
        <Button
          variant="primary"
          onClick={handleGenerate}
          busy={generating}
          busyLabel="Generating..."
        >
          Generate proposal
        </Button>
      </div>

      {sectionsLoaded && sections.length === 0 && (
        <EmptyState title="No proposal sections drafted yet">
          Generate proposal drafts one section per template from this tender's approved analysis.
        </EmptyState>
      )}

      {sections.map((section) => {
        // undefined until this section's listProposalClaims answers: the
        // "No claims" row only appears once the response says so.
        const loadedClaims = claimsBySection[section.id];
        const claims = loadedClaims ?? [];
        return (
          <section key={section.id} className="proposal-section">
            <h2>{section.section_name}</h2>
            <p className="proposal-section__meta">
              <span>Version {section.version}</span>
              <StatusBadge {...describeStatus("proposalSection", section.status)} />
            </p>
            <Field label="Section text">
              <textarea
                className="proposal-section__text"
                rows={6}
                value={editingContent[section.id] ?? section.content}
                onChange={(e) =>
                  setEditingContent((prev) => ({ ...prev, [section.id]: e.target.value }))
                }
              />
            </Field>
            {/* Polish pass (P4): "Save edits" appears only while this
                section has an unsaved draft that differs from the stored
                text, instead of a disabled button on every section. A
                successful save clears the draft (handleSaveContent), so it
                goes again. The spec never reads it (0 matches in
                full-flow.spec.ts). */}
            {editingContent[section.id] !== undefined &&
              editingContent[section.id] !== section.content && (
                <div className="action-row">
                  <Button variant="secondary" onClick={() => handleSaveContent(section.id)}>
                    Save edits
                  </Button>
                </div>
              )}

            <h3>Claims</h3>
            <ul className="claims">
              {claims.map((claim) => (
                <li key={claim.id} className="claim">
                  {/* Polish pass (P4): tag and text in one block, "Inspect
                      trace" on its own line below, instead of trailing a long
                      claim raggedly. The <li> still holds the tag text
                      "/ unsupported]" and the button (:971-974). */}
                  <span className="claim__body">
                    <ClaimTag claim={claim} />{" "}
                    <span className="claim__text">{claim.claim_text}</span>
                  </span>
                  <Button
                    variant="quiet"
                    onClick={(e) => {
                      drawerOpener.current = e.currentTarget;
                      setSelectedClaimId(claim.id);
                    }}
                  >
                    Inspect trace
                  </Button>
                </li>
              ))}
              {loadedClaims !== undefined && loadedClaims.length === 0 && (
                <li className="claims__none">No claims in this section.</li>
              )}
            </ul>
          </section>
        );
      })}

      {selectedClaimId && (
        <TraceabilityDrawer
          claimId={selectedClaimId}
          onClose={() => setSelectedClaimId(null)}
          returnFocusTo={drawerOpener.current}
        />
      )}
    </main>
  );
}

// "[Capability / unsupported]" - the claim's type and grounding. The spec
// finds unsupported claims by the text "/ unsupported]" (full-flow.spec.ts:
// 971, :974), so the grounding badge sits inside the brackets with no space
// before "]", and where its label is just the backend word capitalised
// (grounded, unsupported) its DOM text is that word, drawn capitalised by
// CSS (.badge-raw-word); needs_review and not_required show their mapped
// labels. Brackets and slash are ink-3 punctuation.
function ClaimTag({ claim }: { claim: ProposalClaimResponse }) {
  const grounding = describeStatusKeepingRawWord("grounding", claim.grounding_status);
  return (
    <span className="claim__tag">
      <span className="claim__punct">[</span>
      {humanize(claim.claim_type)}
      <span className="claim__punct"> / </span>
      <span className={grounding.keepsRawWord ? "badge-raw-word" : undefined}>
        <StatusBadge tone={grounding.tone} label={grounding.label} />
      </span>
      <span className="claim__punct">]</span>
    </span>
  );
}
