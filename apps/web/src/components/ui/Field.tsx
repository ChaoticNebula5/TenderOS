import type { ReactNode } from "react";
import "./Field.css";

// A labelled form field with the label ABOVE the control (DESIGN.md
// Components, "Forms and inputs"). The <label> still wraps the control, as
// it does today, so the control's accessible name stays exactly the label
// text - which is what getByLabel queries (full-flow.spec.ts:275-277,
// :510). Any `hint` sits OUTSIDE the <label> for the same reason: text
// inside it would become part of the name and could change what a
// substring getByLabel("Password") matches.
//
// Today's inline label-then-input layout wraps mid-pair at 390px (DESIGN.md
// "Current state", Login at 390px); stacking fixes that.

interface FieldProps {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}

export function Field({ label, hint, children }: FieldProps) {
  return (
    <div className="field">
      <label className="field__label">
        <span className="field__text">{label}</span>
        {children}
      </label>
      {hint && <p className="field__hint">{hint}</p>}
    </div>
  );
}
