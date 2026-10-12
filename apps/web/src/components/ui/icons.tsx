import type { ReactElement, ReactNode } from "react";
import type { StatusTone } from "../../status/statusMap";

// Authored inline SVG icons (DESIGN.md: "icons are authored inline SVG,
// not an icon package"). 16-unit grid, drawn with currentColor so each icon
// takes the hue of the text beside it. Always aria-hidden: the visible
// label next to the icon carries the meaning (Three Signals Rule), so a
// screen reader reads "Approved", not "check icon, Approved".

interface IconProps {
  className?: string;
}

function Svg({ className, children }: IconProps & { children: ReactNode }) {
  return (
    <svg
      className={className}
      viewBox="0 0 16 16"
      width="1em"
      height="1em"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      data-icon=""
    >
      {children}
    </svg>
  );
}

// Ready: check in circle.
function ReadyIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="8" cy="8" r="6.5" />
      <path d="M5.2 8.2 7.2 10.2 10.9 6" />
    </Svg>
  );
}

// Running: half-filled circle.
function RunningIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="8" cy="8" r="6.5" />
      <path d="M8 1.5a6.5 6.5 0 0 1 0 13Z" fill="currentColor" stroke="none" />
    </Svg>
  );
}

// Warning: triangle with bar.
function WarningIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8 1.8 14.6 13.6H1.4Z" />
      <path d="M8 6.2v3.4" />
      <path d="M8 11.7v.1" />
    </Svg>
  );
}

// Blocked: circle with bar (no-entry).
function BlockedIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="8" cy="8" r="6.5" />
      <path d="M4.8 8h6.4" />
    </Svg>
  );
}

// Failed: x in circle.
function FailedIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="8" cy="8" r="6.5" />
      <path d="M5.8 5.8 10.2 10.2M10.2 5.8 5.8 10.2" />
    </Svg>
  );
}

// Neutral: hollow circle (plan item 10).
function NeutralIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="8" cy="8" r="6.5" />
    </Svg>
  );
}

// Info (notices only, not a status): i in circle.
export function InfoIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="8" cy="8" r="6.5" />
      <path d="M8 7.2v4" />
      <path d="M8 4.8v.1" />
    </Svg>
  );
}

const STATUS_ICONS: Record<StatusTone, (props: IconProps) => ReactElement> = {
  ready: ReadyIcon,
  running: RunningIcon,
  warning: WarningIcon,
  blocked: BlockedIcon,
  failed: FailedIcon,
  neutral: NeutralIcon,
};

export function StatusIcon({ tone, className }: IconProps & { tone: StatusTone }) {
  const Icon = STATUS_ICONS[tone];
  return <Icon className={className} />;
}
