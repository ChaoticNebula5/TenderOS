import { Analysis } from "./chapters/Analysis";
import { Eligibility } from "./chapters/Eligibility";
import { Evidence } from "./chapters/Evidence";
import { Impact } from "./chapters/Impact";
import { Overview } from "./chapters/Overview";
import { Submission } from "./chapters/Submission";
import { Footer } from "./components/Footer";
import { Header } from "./components/Header";
import { WorldLayers } from "./components/WorldLayers";
import { useWorld } from "./world/useWorld";

/**
 * Page root, in the reference's DOM order: the fixed world layers, the header, the six
 * chapters, the footer. It has no state, so it renders once; the world runs beside it.
 */
export default function App() {
  useWorld();
  return (
    <>
      <WorldLayers />
      <Header />
      <main>
        <Overview />
        <Analysis />
        <Eligibility />
        <Evidence />
        <Impact />
        <Submission />
      </main>
      <Footer />
    </>
  );
}
