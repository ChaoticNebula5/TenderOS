import { BUYER, TNO, type Evidence as Ev } from "../data/tender";
import { Bars, Foot, Rows, Sig, Top } from "./parts";

/** One evidence file from the company's records. `.ct` marks the line a claim cites. */
export function Evidence({ e, id }: { e: Ev; id: number }) {
  switch (e.k) {
    case "cv":
      return (
        <>
          <Top left={e.f} tag="Résumé" />
          <p className="c-t">{e.name}</p>
          <p className="c-sub">{e.role}</p>
          <p className="c-k">Experience</p>
          {e.rows.map((r, j) => (
            <p key={j} className={j === e.ct ? "c-row ct" : "c-row"}>
              {r}
            </p>
          ))}
          <p className="c-k">Licences and education</p>
          {e.extra.map((r, j) => (
            <p key={j} className="c-row">
              {r}
            </p>
          ))}
          <Bars n={3} s={id} lo={0.4} />
          <Foot left="Page 2 of 3" right="Updated April 2026" />
        </>
      );
    case "cert":
      return (
        <>
          <div className="frame" />
          <div className="ctr">
            <p className="c-small" style={{ marginTop: 14 }}>
              Certificate of registration
            </p>
            <p className="c-t c-big">
              <span className="ct">ISO/IEC 27001:2022</span>
            </p>
            <p className="c-p">Information security management system</p>
            <div className="seal">Valid</div>
            <p className="c-small">
              Scope: design, supply and commissioning of rail signalling and telecom systems
            </p>
            <p className="c-small">
              <b>Valid until 14 March 2027</b>
            </p>
            <Sig />
          </div>
        </>
      );
    case "ca":
      return (
        <>
          <Top left={e.f} tag="Raman and Rao" />
          <p className="c-t">Certificate of turnover</p>
          <p className="c-p">From the audited accounts of the company, turnover was as follows.</p>
          <div style={{ marginTop: 10 }}>
            <Rows
              rows={[
                ["FY 2022-23", "₹61.2 crore"],
                ["FY 2023-24", "₹68.9 crore"],
                ["FY 2024-25", "₹75.1 crore"],
              ]}
            />
            <div className="row ct">
              <span>Average of three years</span>
              <b>₹68.4 crore</b>
            </div>
          </div>
          <p className="c-small" style={{ marginTop: 14 }}>
            Issued at the request of the company for tender purposes only.
          </p>
          <Sig />
          <Foot left="UDIN 25041873BMKX1042" right="Chartered accountants" />
        </>
      );
    case "done":
      return (
        <>
          <Top left={e.f} tag="Completion" />
          <p className="c-t">Completion certificate</p>
          <p className="c-p">
            Certified that the work below was completed to the satisfaction of the Employer.
          </p>
          <div style={{ marginTop: 10 }}>
            <Rows
              rows={[
                ["Project", e.proj],
                ["Work", e.work],
                ["Contract value", e.val],
                ["Completed on", e.date],
                ["Performance", "Satisfactory"],
              ]}
            />
          </div>
          <Sig />
        </>
      );
    case "bg":
      return (
        <>
          <Top left={e.f} tag="Guarantee" />
          <p className="c-t">Bank guarantee for earnest money</p>
          <div style={{ marginTop: 10 }}>
            <Rows
              rows={[
                ["Amount", "₹1.92 crore"],
                ["Beneficiary", BUYER],
                ["Tender", TNO],
                ["Valid until", "31 May 2027"],
              ]}
            />
          </div>
          <Bars n={3} s={id} lo={0.45} />
          <Sig />
        </>
      );
    case "sheet":
      return (
        <>
          <Top left={e.f} tag="Rates" />
          <p className="c-t">Schedule of rates, 2026</p>
          <div style={{ marginTop: 6 }}>
            <Rows
              rows={[
                ["Axle counter, per set", "₹2.4 lakh"],
                ["LED signal, each", "₹1.1 lakh"],
                ["Point machine, each", "₹6.8 lakh"],
                ["Quad cable, per km", "₹3.2 lakh"],
                ["Route interlocking, per route", "₹4.5 lakh"],
                ["Site supervision, per month", "₹9.6 lakh"],
              ]}
            />
          </div>
          <Foot left="Sheet 1 of 3" right="Excl. GST" />
        </>
      );
    case "reg":
      return (
        <>
          <Top left={e.f} tag="Registration" />
          <p className="c-t">{e.title}</p>
          <div style={{ marginTop: 10 }}>
            <Rows rows={e.rows} />
          </div>
          <Bars n={4} s={id} lo={0.4} />
          <Sig />
        </>
      );
  }
}
