import { els, toggleTheme } from "../world/store";

/**
 * Fixed header. The frame loop writes the chapter number and name into #where, and the
 * theme button rewrites its own label, so React renders this once and never again.
 */
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
        <button id="th" type="button" onClick={(e) => toggleTheme(e.currentTarget)}>
          Light
        </button>
      </nav>
    </header>
  );
}
