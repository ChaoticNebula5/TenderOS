import { LABELS, labelEl } from "../world/labels";

/** Every label, rendered once and hidden; the frame loop places and shows them. */
export function Labels() {
  return (
    <div id="labels" aria-hidden="true">
      {LABELS.map((lb, k) => (
        <div
          key={k}
          className={`lb ${lb.cls}${lb.ww ? " wrap" : ""}`}
          ref={(el) => {
            if (el) labelEl[k] = el;
          }}
        >
          {lb.b !== undefined && <b>{lb.b}</b>}
          {lb.span !== undefined && <span>{lb.span}</span>}
          {lb.text}
          {lb.em !== undefined && <em>{lb.em}</em>}
        </div>
      ))}
    </div>
  );
}
