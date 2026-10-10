import type { ButtonHTMLAttributes, ReactNode } from "react";
import { buttonClassName } from "./buttonClassName";
import type { ButtonVariant } from "./buttonClassName";

// The one button (DESIGN.md Components, "Buttons"): primary (amber fill,
// one per region), secondary (raise fill, control border), quiet (text
// only, row actions), danger (failed hue text and border, never a fill).
// One size, 32px - DESIGN.md specifies no other.
//
// busy: the action is in flight. It sets aria-busy, swaps in busyLabel
// (the present participle the screen already shows, "Creating..."), and
// DISABLES the button. Disabling is load-bearing, not cosmetic: the E2E
// waits on toBeEnabled() after long actions (full-flow.spec.ts:949, :1034)
// and on toBeHidden() (:593); a busy-but-enabled button would let those
// waits pass early.
//
// type defaults to "button", not the browser's "submit", so a Button
// dropped into a form never submits it by accident; submit buttons say
// type="submit" explicitly.

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  busy?: boolean;
  busyLabel?: ReactNode;
}

export function Button({
  variant = "secondary",
  busy = false,
  busyLabel,
  disabled,
  type = "button",
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      className={buttonClassName(variant, className)}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
    >
      {busy && busyLabel !== undefined ? busyLabel : children}
    </button>
  );
}
