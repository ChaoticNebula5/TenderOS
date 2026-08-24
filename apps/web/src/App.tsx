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

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <BidDashboard />
              </RequireAuth>
            }
          />
          <Route
            path="/tenders/new"
            element={
              <RequireAuth>
                <CreateBid />
              </RequireAuth>
            }
          />
          <Route
            path="/tenders/:tenderId"
            element={
              <RequireAuth>
                <UploadAndProcessingStatus />
              </RequireAuth>
            }
          />
          <Route
            path="/tenders/:tenderId/requirements"
            element={
              <RequireAuth>
                <RequirementRegister />
              </RequireAuth>
            }
          />
          <Route
            path="/talent"
            element={
              <RequireAuth>
                <TalentLibrary />
              </RequireAuth>
            }
          />
          <Route
            path="/talent/import"
            element={
              <RequireAuth>
                <TalentImportReview />
              </RequireAuth>
            }
          />
          <Route
            path="/talent/employees/:knowledgeItemId"
            element={
              <RequireAuth>
                <EmployeeProfile />
              </RequireAuth>
            }
          />
          <Route
            path="/tenders/:tenderId/compliance"
            element={
              <RequireAuth>
                <ComplianceMatrix />
              </RequireAuth>
            }
          />
          <Route
            path="/tenders/:tenderId/team"
            element={
              <RequireAuth>
                <TeamWorkspace />
              </RequireAuth>
            }
          />
          <Route
            path="/tenders/:tenderId/commercial"
            element={
              <RequireAuth>
                <CommercialFindings />
              </RequireAuth>
            }
          />
          <Route
            path="/tenders/:tenderId/knowledge-selections"
            element={
              <RequireAuth>
                <KnowledgeSelections />
              </RequireAuth>
            }
          />
          <Route
            path="/tenders/:tenderId/proposal"
            element={
              <RequireAuth>
                <ProposalWorkspace />
              </RequireAuth>
            }
          />
          <Route
            path="/tenders/:tenderId/redteam"
            element={
              <RequireAuth>
                <RedteamFindings />
              </RequireAuth>
            }
          />
          <Route
            path="/tenders/:tenderId/graph"
            element={
              <RequireAuth>
                <GraphExplorer />
              </RequireAuth>
            }
          />
          <Route
            path="/tenders/:tenderId/readiness"
            element={
              <RequireAuth>
                <Readiness />
              </RequireAuth>
            }
          />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
