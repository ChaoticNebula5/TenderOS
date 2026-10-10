import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { BidDashboard } from "./routes/BidDashboard";
import { ComplianceMatrix } from "./routes/ComplianceMatrix";
import { CommercialFindings } from "./routes/CommercialFindings";
import { CreateBid } from "./routes/CreateBid";
import { EmployeeProfile } from "./routes/EmployeeProfile";
import { GraphExplorer } from "./routes/GraphExplorer";
import { KnowledgeSelections } from "./routes/KnowledgeSelections";
import { Login } from "./routes/Login";
import { ProposalWorkspace } from "./routes/ProposalWorkspace";
import { Readiness } from "./routes/Readiness";
import { RedteamFindings } from "./routes/RedteamFindings";
import { RequirementRegister } from "./routes/RequirementRegister";
import { TalentImportReview } from "./routes/TalentImportReview";
import { TalentLibrary } from "./routes/TalentLibrary";
import { TeamWorkspace } from "./routes/TeamWorkspace";
import { UploadAndProcessingStatus } from "./routes/UploadAndProcessingStatus";
import { AppShell, PageContent } from "./shell/AppShell";
import { TenderLayout } from "./shell/TenderLayout";

// Same 15 paths and screens as before restyle batch 2; only the nesting
// changed. One RequireAuth wraps the shell (a layout route) instead of one
// RequireAuth per route, so the shell stays mounted across navigation and
// only the screen below it swaps. Login stays outside both.
//
// Tender routes nest under /tenders/:tenderId so TenderLayout can draw the
// workflow nav; child screens still read tenderId from useParams, which
// includes parent params. "/tenders/new" is a static segment and still
// outranks ":tenderId", as it did when both were siblings.
//
// Own export, separate from App's BrowserRouter, so AppRoutes.test.tsx can
// render the real route tree inside a MemoryRouter.
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route element={<PageContent />}>
          <Route path="/" element={<BidDashboard />} />
          <Route path="/tenders/new" element={<CreateBid />} />
          <Route path="/talent" element={<TalentLibrary />} />
          <Route path="/talent/import" element={<TalentImportReview />} />
          <Route path="/talent/employees/:knowledgeItemId" element={<EmployeeProfile />} />
        </Route>
        <Route path="/tenders/:tenderId" element={<TenderLayout />}>
          <Route index element={<UploadAndProcessingStatus />} />
          <Route path="requirements" element={<RequirementRegister />} />
          <Route path="compliance" element={<ComplianceMatrix />} />
          <Route path="team" element={<TeamWorkspace />} />
          <Route path="commercial" element={<CommercialFindings />} />
          <Route path="knowledge-selections" element={<KnowledgeSelections />} />
          <Route path="proposal" element={<ProposalWorkspace />} />
          <Route path="redteam" element={<RedteamFindings />} />
          <Route path="graph" element={<GraphExplorer />} />
          <Route path="readiness" element={<Readiness />} />
        </Route>
      </Route>
    </Routes>
  );
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
