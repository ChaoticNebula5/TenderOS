import { hash } from "../world/math";

/** Grey text bars standing in for body copy. Widths are seeded so each page is stable. */
export function Bars({ n, s, lo = 0.5 }: { n: number; s: number; lo?: number }) {
  return (
    <>
      {Array.from({ length: n }, (_, j) => (
        <i
          key={j}
          className="rb"
          style={{ width: `${Math.round((lo + hash(s * 13 + j) * (1 - lo)) * 100)}%` }}
        />
      ))}
    </>
  );
}

/** Label and value rows, as on certificates and schedules. */
export function Rows({ rows }: { rows: readonly (readonly [string, string])[] }) {
  return (
    <>
      {rows.map(([k, v]) => (
        <div className="row" key={k}>
          <span>{k}</span>
          <b>{v}</b>
        </div>
      ))}
    </>
  );
}

/** Document header: file or issuer on the left, tag on the right. */
export function Top({ left, tag }: { left: string; tag: string }) {
  return (
    <div className="c-top">
      <span>{left}</span>
      <b>{tag}</b>
    </div>
  );
}

/** Two signature lines. */
export function Sig() {
  return (
    <div className="sig">
      <i />
      <i />
    </div>
  );
}

export function Foot({ left, right }: { left: string; right: string }) {
  return (
    <div className="c-foot">
      <span>{left}</span>
      <span>{right}</span>
    </div>
  );
}
