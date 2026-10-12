import { Fragment } from "react";

// A backend identifier or file name ("deselected_fact_in_use",
// "resume_avery_quill.docx") with a line-break opportunity after each
// underscore, so at phone width it wraps at "deselected_ / fact_ / in_use"
// rather than mid-word or not at all. <wbr> adds no text: the element's
// textContent stays exactly the input, which full-flow.spec.ts relies on
// where it reads the red-team category (:1380, :1409 match it exactly).
//
// Shared since the polish pass; batches 5 and 6 each had a local copy
// (RedteamFindings' category cell, TalentImportReview's source cell).
export function BreakAfterUnderscores({ text }: { text: string }) {
  const parts = text.split("_");
  return (
    <>
      {parts.map((part, i) => (
        <Fragment key={i}>
          {part}
          {i < parts.length - 1 && (
            <>
              _<wbr />
            </>
          )}
        </Fragment>
      ))}
    </>
  );
}
