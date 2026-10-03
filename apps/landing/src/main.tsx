import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Order matters: responsive.css overrides the others, as in the reference's single sheet.
import "./styles/tokens.css";
import "./styles/world.css";
import "./styles/documents.css";
import "./styles/chrome.css";
import "./styles/chapters.css";
import "./styles/responsive.css";
import App from "./App";

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("#root element missing from index.html");

createRoot(rootEl).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
