import { els } from "../world/store";

/** Fixed header. The frame loop writes the chapter number and name into #where. */
export function Header() {
  return (
    <header>
      <a className="mark" href="#top">
        Tender<span>OS</span>
      </a>
      <p id="where" aria-live="polite">
        <b
          ref={(el) => {
            els.whereN = el;
          }}
        >
          1
        </b>
        <span
          ref={(el) => {
            els.whereT = el;
          }}
        >
          Overview
        </span>
      </p>
      <nav>
        <a href="#pack">Analysis</a>
        <a href="#proof">Evidence</a>
        <a href="#stress">Impact</a>
        <button id="th" type="button">
          Light
        </button>
      </nav>
    </header>
  );
}
