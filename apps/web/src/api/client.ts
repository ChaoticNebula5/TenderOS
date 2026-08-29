// Typed fetch wrapper - one function per Part G route actually consumed by
// a screen, added as that screen is built (§7 of the B13 plan), not all up
// front. Request/response types mirror the real Pydantic schemas
// (apps/api/src/tenderos_api/schemas/*.py) - not invented client-side
// shapes. No TanStack Query or similar: "function before visual polish"
// (Part J) doesn't call for it, and this project's locked stack (CLAUDE.md)
// doesn't name one.

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

const ACCESS_TOKEN_KEY = "tenderos_access_token";
const REFRESH_TOKEN_KEY = "tenderos_refresh_token";

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function setTokens(accessToken: string, refreshToken: string): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
}

export function setAccessToken(accessToken: string): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown) {
    // tsconfig's erasableSyntaxOnly forbids parameter properties (they
    // require emitting assignment code, not just erasing types) - fields
    // declared and assigned explicitly instead.
    super(`API error ${status}: ${JSON.stringify(body)}`);
    this.status = status;
    this.body = body;
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  auth?: boolean; // default true - most routes require it, G1's own four don't uniformly
  idempotencyKey?: string; // Part I1 - required for the six operations it names
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let parsedBody: unknown = null;
    try {
      parsedBody = await response.json();
    } catch {
      // Non-JSON error body (e.g. a bare 500) - ApiError still carries the
      // status; the caller decides how to render an unparseable error.
    }
    throw new ApiError(response.status, parsedBody);
  }
  // 204 is the only status this used to special-case, but
  // uploadKnowledgeDocument documents a real 202-with-no-body response
  // (extraction hasn't run yet) - awaiting .json() on an empty body
  // throws a SyntaxError, failing every successful call. Checking the
  // body text itself, not a fixed status list, covers that and any
  // other no-content 2xx response without hard-coding which statuses
  // the API happens to use that way today.
  const text = await response.text();
  if (text === "") return undefined as T;
  return JSON.parse(text) as T;
}

// Not exported - every route gets its own named function below (or in a
// screen's own api/*.ts module as screens are built), never called with a
// raw path from outside this module. Keeps the "one function per Part G
// route" contract real rather than a suggestion.
async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, auth = true, idempotencyKey } = options;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (auth) {
    const token = getAccessToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;

  const response = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  return handleResponse<T>(response);
}

// Separate from apiFetch, not a variant of it: a multipart body must not
// carry a JSON Content-Type header (the browser sets its own, including
// the multipart boundary) and is never JSON.stringify'd.
async function apiUpload<T>(path: string, file: File): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getAccessToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers,
    body: formData,
  });

  return handleResponse<T>(response);
}

// uploadResumes (G6) is the one route taking multiple files under one
// field name ("files", matching routes/knowledge.py's own UploadFile
// list parameter) - a second small helper rather than overloading
// apiUpload's single-file signature.
async function apiUploadMultiple<T>(path: string, files: File[]): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getAccessToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const formData = new FormData();
  for (const file of files) formData.append("files", file);

  const response = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers,
    body: formData,
  });

  return handleResponse<T>(response);
}

// ----------------------------------------------------------------------
// G1. Auth - mirrors apps/api/src/tenderos_api/schemas/auth.py exactly
// ----------------------------------------------------------------------

export interface UserResponse {
  id: string;
  email: string;
  display_name: string | null;
  is_active: boolean;
}

export interface RegisterRequest {
  email: string;
  password: string;
  organization_name: string;
  display_name?: string | null;
}

export interface RegisterResponse {
  user: UserResponse;
  organization_id: string;
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface MeMembership {
  organization_id: string;
  role: string;
}

export interface MeResponse {
  user: UserResponse;
  memberships: MeMembership[];
}

export function register(body: RegisterRequest): Promise<RegisterResponse> {
  return apiFetch<RegisterResponse>("/auth/register", { method: "POST", body, auth: false });
}

export function login(body: LoginRequest): Promise<TokenResponse> {
  return apiFetch<TokenResponse>("/auth/login", { method: "POST", body, auth: false });
}

export function me(): Promise<MeResponse> {
  return apiFetch<MeResponse>("/auth/me");
}

// ----------------------------------------------------------------------
// G2. Tenders - mirrors apps/api/src/tenderos_api/schemas/tenders.py
// ----------------------------------------------------------------------

export interface TenderResponse {
  id: string;
  organization_id: string;
  name: string;
  client_name: string | null;
  submission_date: string | null;
  status: string;
  state_version: number;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface TenderCreateRequest {
  name: string;
  client_name?: string | null;
  submission_date?: string | null;
}

export function listTenders(orgId: string): Promise<TenderResponse[]> {
  return apiFetch<TenderResponse[]>(`/organizations/${orgId}/tenders`);
}

export function createTender(orgId: string, body: TenderCreateRequest): Promise<TenderResponse> {
  return apiFetch<TenderResponse>(`/organizations/${orgId}/tenders`, { method: "POST", body });
}

export function getTender(tenderId: string): Promise<TenderResponse> {
  return apiFetch<TenderResponse>(`/tenders/${tenderId}`);
}

// ----------------------------------------------------------------------
// G2. Documents - mirrors apps/api/src/tenderos_api/schemas/documents.py
// ----------------------------------------------------------------------

export interface DocumentResponse {
  id: string;
  tender_id: string | null;
  organization_id: string | null;
  side: string;
  doc_type: string;
  filename: string;
  sha256: string;
  mime_type: string | null;
  file_size_bytes: number | null;
  page_count: number | null;
  parse_status: string;
  version: number;
  created_at: string;
}

export function listDocuments(tenderId: string): Promise<DocumentResponse[]> {
  return apiFetch<DocumentResponse[]>(`/tenders/${tenderId}/documents`);
}

export function uploadDocument(tenderId: string, file: File): Promise<DocumentResponse> {
  return apiUpload<DocumentResponse>(`/tenders/${tenderId}/documents`, file);
}

export function reprocessDocument(documentId: string): Promise<DocumentResponse> {
  return apiFetch<DocumentResponse>(`/documents/${documentId}/reprocess`, { method: "POST" });
}

// ----------------------------------------------------------------------
// G3. Workflow - mirrors apps/api/src/tenderos_api/schemas/workflow.py
// ----------------------------------------------------------------------

export interface WorkflowRunResponse {
  id: string;
  tender_id: string;
  langgraph_thread_id: string;
  status: string;
  plan_version: number;
  prompt_bundle_version: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface WorkflowTaskResponse {
  id: string;
  workflow_run_id: string;
  task_key: string;
  task_type: string;
  assigned_agent: string;
  status: string;
  priority: number;
  attempt_count: number;
  max_attempts: number;
  error_code: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export function createWorkflowRun(
  tenderId: string,
  idempotencyKey: string,
): Promise<WorkflowRunResponse> {
  return apiFetch<WorkflowRunResponse>(`/tenders/${tenderId}/workflow-runs`, {
    method: "POST",
    idempotencyKey,
  });
}

// Newest-first (the repository's own ordering) - a tender can have
// several runs; picking which one counts as "the" active one is the
// caller's decision, not this function's.
export function listWorkflowRuns(tenderId: string): Promise<WorkflowRunResponse[]> {
  return apiFetch<WorkflowRunResponse[]>(`/tenders/${tenderId}/workflow-runs`);
}

export function getWorkflowRun(runId: string): Promise<WorkflowRunResponse> {
  return apiFetch<WorkflowRunResponse>(`/workflow-runs/${runId}`);
}

export function listWorkflowTasks(runId: string): Promise<WorkflowTaskResponse[]> {
  return apiFetch<WorkflowTaskResponse[]>(`/workflow-runs/${runId}/tasks`);
}

// B15: not in Part G's locked route list (same precedent as getEmployee
// above) - mints the short-lived, single-use ticket useEventStream.ts uses
// in place of the real access JWT, which used to ride in the SSE URL's own
// query string for its full 15-minute life (KNOWN_LIMITATIONS.md).
export interface SSETicketResponse {
  ticket: string;
  expires_in_seconds: number;
}

export function mintWorkflowEventsTicket(runId: string): Promise<SSETicketResponse> {
  return apiFetch<SSETicketResponse>(`/workflow-runs/${runId}/events/ticket`, { method: "POST" });
}

// ----------------------------------------------------------------------
// G4. Gates - mirrors apps/api/src/tenderos_api/schemas/gates.py's
// discriminated union exactly, gate_type by gate_type. TypeScript narrows
// `gate.summary`'s fields from a `switch`/`if` on `gate.gate_type` the
// same way Pydantic narrows on the backend - the two are meant to be read
// side by side, not just kept "compatible."
// ----------------------------------------------------------------------

export interface GateTargetRef {
  target_type: string;
  target_id: string;
}

interface GateResponseBase {
  gate_id: string;
  workflow_run_id: string;
  status: string;
  targets: GateTargetRef[];
  allowed_decisions: string[];
  decision_payload: Record<string, unknown> | null;
  decided_by: string | null;
  requested_at: string;
  decided_at: string | null;
}

export interface RequirementReviewGateResponse extends GateResponseBase {
  gate_type: "requirement_review";
  summary: {
    requirement_count: number;
    mandatory_count: number;
    unverified_mandatory_count: number;
    submission_item_count: number;
    clarification_count: number;
  };
}

export interface ComplianceReviewGateResponse extends GateResponseBase {
  gate_type: "compliance_review";
  summary: { compliance_entry_count: number; pending_count: number; high_risk_count: number };
}

export interface ResourceSelectionGateResponse extends GateResponseBase {
  gate_type: "resource_selection";
  summary: { role_count: number; candidate_count: number; unselected_count: number };
}

export interface CommercialApprovalGateResponse extends GateResponseBase {
  gate_type: "commercial_approval";
  summary: { finding_count: number; pending_count: number; critical_count: number };
}

export interface FindingResolutionGateResponse extends GateResponseBase {
  gate_type: "finding_resolution";
  summary: { open_finding_count: number; critical_count: number };
}

export interface ReadinessBlocker {
  category: string;
  target_type: string;
  target_id: string | null;
  message: string;
}

export interface FinalReadinessGateResponse extends GateResponseBase {
  gate_type: "final_readiness";
  summary: {
    ready: boolean;
    blockers: ReadinessBlocker[];
    counts: Record<string, number> | null;
    degraded_stages: string[];
  };
}

export type GateResponse =
  | RequirementReviewGateResponse
  | ComplianceReviewGateResponse
  | ResourceSelectionGateResponse
  | CommercialApprovalGateResponse
  | FindingResolutionGateResponse
  | FinalReadinessGateResponse;

export interface TargetUpdate {
  target_type: string;
  target_id: string;
  decision: "approve";
}

export interface GateDecisionRequest {
  decision: "approve" | "reject" | "request_changes";
  target_updates?: TargetUpdate[];
  notes?: string | null;
  waive?: boolean;
}

export function listGates(runId: string): Promise<GateResponse[]> {
  return apiFetch<GateResponse[]>(`/workflow-runs/${runId}/gates`);
}

export function getGate(gateId: string): Promise<GateResponse> {
  return apiFetch<GateResponse>(`/gates/${gateId}`);
}

export function decideGate(
  gateId: string,
  body: GateDecisionRequest,
  idempotencyKey: string,
): Promise<GateResponse> {
  return apiFetch<GateResponse>(`/gates/${gateId}/decision`, {
    method: "POST",
    body,
    idempotencyKey,
  });
}

// ----------------------------------------------------------------------
// G5. Requirements - mirrors apps/api/src/tenderos_api/schemas/
// requirements.py. merge/split are real routes with no screen consuming
// them yet (B13 plan's own §3a inventory) - not wired here; list/patch/
// verify are screen 8's actual scope.
// ----------------------------------------------------------------------

export interface RequirementResponse {
  id: string;
  tender_id: string;
  display_code: string;
  requirement_text: string;
  category: string;
  mandatory: boolean;
  source_chunk_id: string;
  confidence: number | null;
  verified: boolean;
  human_reviewed: boolean;
  rejected_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PatchRequirementRequest {
  requirement_text?: string | null;
  category?: string | null;
  mandatory?: boolean | null;
  action?: "confirm" | "reject" | null;
  notes?: string | null;
}

export function listRequirements(tenderId: string): Promise<RequirementResponse[]> {
  return apiFetch<RequirementResponse[]>(`/tenders/${tenderId}/requirements`);
}

export function patchRequirement(
  requirementId: string,
  body: PatchRequirementRequest,
): Promise<RequirementResponse> {
  return apiFetch<RequirementResponse>(`/requirements/${requirementId}`, {
    method: "PATCH",
    body,
  });
}

export function verifyRequirement(requirementId: string): Promise<RequirementResponse> {
  return apiFetch<RequirementResponse>(`/requirements/${requirementId}/verify`, {
    method: "POST",
  });
}

// ----------------------------------------------------------------------
// G6. Company Knowledge and Talent Onboarding - mirrors
// apps/api/src/tenderos_api/schemas/knowledge.py. Screens 2 (talent
// library), 3 (import batches/review queue), 4 (employee profile detail/
// verification).
// ----------------------------------------------------------------------

export interface KnowledgeItemResponse {
  id: string;
  organization_id: string;
  item_type: string;
  title: string;
  status: string;
  valid_from: string | null;
  valid_until: string | null;
  source_document_id: string | null;
  source_chunk_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface EmployeeResponse {
  knowledge_item_id: string;
  organization_id: string;
  employee_code: string | null;
  name: string;
  email: string | null;
  current_title: string | null;
  years_experience: number | null;
  professional_location: string | null;
  availability_status: string;
  availability_as_of: string | null;
  eligible_for_bid: boolean;
  profile_review_status: string;
  profile_last_verified_at: string | null;
  profile_verified_by: string | null;
  resume_document_id: string | null;
}

export interface ImportBatchResponse {
  id: string;
  organization_id: string;
  import_type: string;
  source_document_id: string | null;
  status: string;
  total_items: number;
  completed_items: number;
  failed_items: number;
  created_at: string;
}

export interface ImportItemResponse {
  id: string;
  batch_id: string;
  source_ref: string;
  status: string;
  target_knowledge_item_id: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface ImportItemDetailResponse extends ImportItemResponse {
  extracted_payload: Record<string, unknown>;
}

export interface PatchKnowledgeItemRequest {
  title?: string | null;
  status?: "active" | "expired" | "archived" | "unverified" | null;
}

export interface PatchEmployeeProfileRequest {
  name?: string | null;
  email?: string | null;
  current_title?: string | null;
  professional_location?: string | null;
  years_experience?: number | null;
  availability_status?: "available" | "partially_available" | "unavailable" | "unknown" | null;
  availability_as_of?: string | null;
}

export function uploadEmployeeMaster(orgId: string, file: File): Promise<ImportBatchResponse> {
  return apiUpload<ImportBatchResponse>(
    `/organizations/${orgId}/knowledge/import-batches/employee-master`,
    file,
  );
}

export function uploadCertifications(orgId: string, file: File): Promise<ImportBatchResponse> {
  return apiUpload<ImportBatchResponse>(
    `/organizations/${orgId}/knowledge/import-batches/certifications`,
    file,
  );
}

export function uploadResumes(orgId: string, files: File[]): Promise<ImportBatchResponse> {
  return apiUploadMultiple<ImportBatchResponse>(
    `/organizations/${orgId}/knowledge/import-batches/resumes`,
    files,
  );
}

// 202, no body (route returns None - extraction hasn't run yet, poll
// listOrganizationKnowledge for the result).
export function uploadKnowledgeDocument(orgId: string, file: File): Promise<void> {
  return apiUpload<void>(`/organizations/${orgId}/knowledge/documents`, file);
}

export function listImportBatches(orgId: string): Promise<ImportBatchResponse[]> {
  return apiFetch<ImportBatchResponse[]>(`/organizations/${orgId}/knowledge/import-batches`);
}

export function getImportBatch(batchId: string): Promise<ImportBatchResponse> {
  return apiFetch<ImportBatchResponse>(`/knowledge/import-batches/${batchId}`);
}

export function listImportItems(batchId: string): Promise<ImportItemDetailResponse[]> {
  return apiFetch<ImportItemDetailResponse[]>(`/knowledge/import-batches/${batchId}/items`);
}

export function approveImportItem(itemId: string): Promise<ImportItemResponse> {
  return apiFetch<ImportItemResponse>(`/knowledge/import-items/${itemId}/approve`, {
    method: "POST",
  });
}

export function rejectImportItem(
  itemId: string,
  reason: string | null,
): Promise<ImportItemResponse> {
  return apiFetch<ImportItemResponse>(`/knowledge/import-items/${itemId}/reject`, {
    method: "POST",
    body: { reason },
  });
}

export function listOrganizationKnowledge(
  orgId: string,
  itemType?: string,
): Promise<KnowledgeItemResponse[]> {
  const query = itemType ? `?item_type=${encodeURIComponent(itemType)}` : "";
  return apiFetch<KnowledgeItemResponse[]>(`/organizations/${orgId}/knowledge${query}`);
}

export function listBidEligibleEmployees(orgId: string): Promise<EmployeeResponse[]> {
  return apiFetch<EmployeeResponse[]>(`/organizations/${orgId}/employees`);
}

export function getKnowledgeItem(knowledgeItemId: string): Promise<KnowledgeItemResponse> {
  return apiFetch<KnowledgeItemResponse>(`/knowledge/${knowledgeItemId}`);
}

export function patchKnowledgeItem(
  knowledgeItemId: string,
  body: PatchKnowledgeItemRequest,
): Promise<KnowledgeItemResponse> {
  return apiFetch<KnowledgeItemResponse>(`/knowledge/${knowledgeItemId}`, {
    method: "PATCH",
    body,
  });
}

// B13: added alongside screen 4 - Part G's own G6 list never named this
// route (see KNOWN_LIMITATIONS.md). Reuses the same manage_organization_
// knowledge action every other G6 route uses.
export function getEmployee(knowledgeItemId: string): Promise<EmployeeResponse> {
  return apiFetch<EmployeeResponse>(`/employees/${knowledgeItemId}`);
}

export function patchEmployeeProfile(
  knowledgeItemId: string,
  body: PatchEmployeeProfileRequest,
): Promise<EmployeeResponse> {
  return apiFetch<EmployeeResponse>(`/employees/${knowledgeItemId}/profile`, {
    method: "PATCH",
    body,
  });
}

export function verifyEmployee(
  knowledgeItemId: string,
  eligibleForBid: boolean,
): Promise<EmployeeResponse> {
  return apiFetch<EmployeeResponse>(`/employees/${knowledgeItemId}/verify`, {
    method: "POST",
    body: { eligible_for_bid: eligibleForBid },
  });
}

export function replaceEmployeeResume(
  knowledgeItemId: string,
  file: File,
): Promise<EmployeeResponse> {
  return apiUpload<EmployeeResponse>(`/employees/${knowledgeItemId}/resume`, file);
}

// ----------------------------------------------------------------------
// G7. Compliance - mirrors apps/api/src/tenderos_api/schemas/compliance.py.
// Screen 9 (Compliance matrix), reuses GateDecisionPanel for the
// compliance_review gate.
// ----------------------------------------------------------------------

export interface ComplianceResponse {
  id: string;
  requirement_id: string;
  status: string;
  reasoning_summary: string | null;
  confidence: number | null;
  human_approved: boolean;
  last_evaluated_at: string;
  updated_at: string;
}

export interface PatchComplianceRequest {
  status?: string | null;
  reasoning_summary?: string | null;
}

export function listCompliance(tenderId: string): Promise<ComplianceResponse[]> {
  return apiFetch<ComplianceResponse[]>(`/tenders/${tenderId}/compliance`);
}

export function patchCompliance(
  complianceId: string,
  body: PatchComplianceRequest,
): Promise<ComplianceResponse> {
  return apiFetch<ComplianceResponse>(`/compliance/${complianceId}`, { method: "PATCH", body });
}

export function approveCompliance(complianceId: string): Promise<ComplianceResponse> {
  return apiFetch<ComplianceResponse>(`/compliance/${complianceId}/approve`, { method: "POST" });
}

// ----------------------------------------------------------------------
// G8. Talent - mirrors apps/api/src/tenderos_api/schemas/talent.py.
// Screen 11 (Team workspace), reuses GateDecisionPanel for the
// resource_selection gate. select-candidate is called directly, never
// through the gate's own target_updates - see KNOWN_LIMITATIONS.md.
// ----------------------------------------------------------------------

export interface RoleRequirementResponse {
  id: string;
  tender_id: string;
  role_name: string;
  min_experience_years: number | null;
  required_skills: unknown[];
  required_certs: unknown[];
}

export interface RoleMatchResponse {
  id: string;
  role_requirement_id: string;
  employee_knowledge_item_id: string;
  score: number | null;
  strengths: unknown[];
  gaps: unknown[];
  human_selected: boolean;
}

export function listRoles(tenderId: string): Promise<RoleRequirementResponse[]> {
  return apiFetch<RoleRequirementResponse[]>(`/tenders/${tenderId}/roles`);
}

export function listMatches(roleId: string): Promise<RoleMatchResponse[]> {
  return apiFetch<RoleMatchResponse[]>(`/roles/${roleId}/matches`);
}

export function selectCandidate(
  roleId: string,
  employeeKnowledgeItemId: string,
  selected: boolean,
  idempotencyKey: string,
): Promise<RoleMatchResponse> {
  return apiFetch<RoleMatchResponse>(`/roles/${roleId}/select-candidate`, {
    method: "POST",
    body: { employee_knowledge_item_id: employeeKnowledgeItemId, selected },
    idempotencyKey,
  });
}

// ----------------------------------------------------------------------
// G9. Commercial - mirrors apps/api/src/tenderos_api/schemas/commercial.py.
// Screen 12 (Commercial findings), reuses GateDecisionPanel for the
// commercial_approval gate.
// ----------------------------------------------------------------------

export interface PricingDocumentResponse {
  id: string;
  tender_id: string;
  document_id: string;
  sheet_name: string;
  currency: string | null;
}

export interface PricingFindingResponse {
  id: string;
  tender_id: string;
  finding_type: string;
  item_a_ref: Record<string, unknown> | null;
  item_b_ref: Record<string, unknown> | null;
  delta: number | null;
  severity: string;
  note: string;
  resolved: boolean;
  created_at: string;
}

export function listPricingDocuments(tenderId: string): Promise<PricingDocumentResponse[]> {
  return apiFetch<PricingDocumentResponse[]>(`/tenders/${tenderId}/pricing-documents`);
}

export function validatePricingDocument(
  pricingDocumentId: string,
): Promise<PricingFindingResponse[]> {
  return apiFetch<PricingFindingResponse[]>(`/pricing-documents/${pricingDocumentId}/validate`, {
    method: "POST",
  });
}

export function listPricingFindings(tenderId: string): Promise<PricingFindingResponse[]> {
  return apiFetch<PricingFindingResponse[]>(`/tenders/${tenderId}/pricing-findings`);
}

export function resolvePricingFinding(pricingFindingId: string): Promise<PricingFindingResponse> {
  return apiFetch<PricingFindingResponse>(`/pricing-findings/${pricingFindingId}/resolve`, {
    method: "POST",
  });
}

// ----------------------------------------------------------------------
// G6. Tender knowledge selections - screen 10. §3c: the PUT now requires
// Idempotency-Key (KNOWN_LIMITATIONS.md).
// ----------------------------------------------------------------------

export interface KnowledgeSelectionResponse {
  id: string;
  tender_id: string;
  knowledge_item_id: string;
  selected: boolean;
  selected_at: string | null;
  selected_by: string | null;
  deselected_at: string | null;
  deselected_by: string | null;
  change_reason: string | null;
}

export function listKnowledgeSelections(tenderId: string): Promise<KnowledgeSelectionResponse[]> {
  return apiFetch<KnowledgeSelectionResponse[]>(`/tenders/${tenderId}/knowledge-selections`);
}

export function setKnowledgeSelection(
  tenderId: string,
  knowledgeItemId: string,
  selected: boolean,
  changeReason: string | null,
  idempotencyKey: string,
): Promise<KnowledgeSelectionResponse> {
  return apiFetch<KnowledgeSelectionResponse>(
    `/tenders/${tenderId}/knowledge-selections/${knowledgeItemId}`,
    {
      method: "PUT",
      body: { selected, change_reason: changeReason },
      idempotencyKey,
    },
  );
}

// ----------------------------------------------------------------------
// G12. Graph - mirrors apps/api/src/tenderos_api/schemas/graph.py. Screen
// 10's own impact-preview flow: Part G's own G6 documented POST .../
// impact-preview was never built (KNOWN_LIMITATIONS.md) - the real
// capability lives here, at POST .../graph/impact, instead.
// ----------------------------------------------------------------------

export interface GraphImpactRow {
  [key: string]: unknown;
}

export function triggerGraphProjection(tenderId: string, idempotencyKey: string): Promise<void> {
  return apiFetch<void>(`/tenders/${tenderId}/graph/project`, {
    method: "POST",
    idempotencyKey,
  });
}

export function previewGraphImpact(
  tenderId: string,
  knowledgeItemId: string,
): Promise<GraphImpactRow[]> {
  return apiFetch<GraphImpactRow[]>(`/tenders/${tenderId}/graph/impact`, {
    method: "POST",
    body: { knowledge_item_id: knowledgeItemId },
  });
}

// ----------------------------------------------------------------------
// G10. Proposal workspace - screens 13/16. listProposalClaims is a B13
// addition (KNOWN_LIMITATIONS.md: no route ever exposed a section's claim
// ids before this block) - not in Part G's original documented surface.
// ----------------------------------------------------------------------

export interface ProposalSectionResponse {
  id: string;
  tender_id: string;
  section_name: string;
  content: string;
  version: number;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ProposalClaimResponse {
  id: string;
  section_id: string;
  claim_text: string;
  claim_type: string;
  is_factual: boolean;
  grounding_status: string;
}

export interface ClaimEvidenceTraceResponse {
  evidence_link_id: string;
  source_kind: string;
  excerpt: string | null;
  verified: boolean;
  validity_status: string;
  knowledge_item_id: string | null;
  knowledge_item_title: string | null;
  document_id: string | null;
  document_filename: string | null;
}

export interface ProposalClaimTraceResponse {
  claim_id: string;
  claim_text: string;
  claim_type: string;
  is_factual: boolean;
  grounding_status: string;
  section_id: string;
  section_name: string;
  requirement_ids: string[];
  employee_knowledge_item_ids: string[];
  evidence: ClaimEvidenceTraceResponse[];
  currently_in_scope_evidence: ClaimEvidenceTraceResponse[];
  section_is_data_gated: boolean;
}

export function listProposalSections(tenderId: string): Promise<ProposalSectionResponse[]> {
  return apiFetch<ProposalSectionResponse[]>(`/tenders/${tenderId}/proposal/sections`);
}

export function generateProposal(
  tenderId: string,
  idempotencyKey: string,
): Promise<ProposalSectionResponse[]> {
  return apiFetch<ProposalSectionResponse[]>(`/tenders/${tenderId}/proposal/generate`, {
    method: "POST",
    idempotencyKey,
  });
}

export function listProposalClaims(sectionId: string): Promise<ProposalClaimResponse[]> {
  return apiFetch<ProposalClaimResponse[]>(`/proposal/sections/${sectionId}/claims`);
}

export function patchProposalSection(
  sectionId: string,
  content: string,
): Promise<ProposalSectionResponse> {
  return apiFetch<ProposalSectionResponse>(`/proposal/sections/${sectionId}`, {
    method: "PATCH",
    body: { content },
  });
}

export function traceProposalClaim(claimId: string): Promise<ProposalClaimTraceResponse> {
  return apiFetch<ProposalClaimTraceResponse>(`/proposal/claims/${claimId}/trace`);
}

// ----------------------------------------------------------------------
// G11. Red-team findings - screen 14. triggerRedTeam calls the real
// Gemini API - see KNOWN_LIMITATIONS.md's own doc-gap entry (this route
// exists in code, undocumented in Part G's G11 section).
// ----------------------------------------------------------------------

export interface RedteamFindingResponse {
  id: string;
  tender_id: string;
  target_type: string;
  target_id: string | null;
  finding_category: string;
  severity: string;
  finding_text: string;
  rebuttal: string | null;
  evidence_refs: unknown[];
  violated_condition: string;
  resolved: boolean;
  resolution_note: string | null;
  created_at: string;
}

export function triggerRedTeam(tenderId: string): Promise<RedteamFindingResponse[]> {
  return apiFetch<RedteamFindingResponse[]>(`/tenders/${tenderId}/redteam/run`, {
    method: "POST",
  });
}

export function listRedteamFindings(tenderId: string): Promise<RedteamFindingResponse[]> {
  return apiFetch<RedteamFindingResponse[]>(`/tenders/${tenderId}/redteam-findings`);
}

export function resolveRedteamFinding(
  findingId: string,
  resolutionNote: string | null,
): Promise<RedteamFindingResponse> {
  return apiFetch<RedteamFindingResponse>(`/redteam-findings/${findingId}/resolve`, {
    method: "POST",
    body: { resolution_note: resolutionNote },
  });
}

// ----------------------------------------------------------------------
// G11. Readiness - screen 15.
// ----------------------------------------------------------------------

export interface ReadinessBlocker {
  category: string;
  target_type: string;
  target_id: string | null;
  message: string;
}

export interface ReadinessCounts {
  requirements_total: number;
  requirements_completed: number;
  mandatory_requirements_unresolved: number;
  compliance_decisions_awaiting_approval: number;
  requirements_lacking_valid_evidence: number;
  critical_major_redteam_findings: number;
  missing_submission_artefacts: number;
  commercial_findings_open: number;
  resource_gaps: number;
  proposal_claims_needing_review: number;
  open_clarifications: number;
}

export interface ReadinessReportResponse {
  id: string;
  tender_id: string;
  tender_state_version: number;
  counts: ReadinessCounts;
  blockers: ReadinessBlocker[];
  ready: boolean;
  generated_at: string;
}

export function getReadiness(tenderId: string): Promise<ReadinessReportResponse> {
  return apiFetch<ReadinessReportResponse>(`/tenders/${tenderId}/readiness`);
}

export function generateReadiness(tenderId: string): Promise<ReadinessReportResponse> {
  return apiFetch<ReadinessReportResponse>(`/tenders/${tenderId}/readiness/generate`, {
    method: "POST",
  });
}

// ----------------------------------------------------------------------
// G12. Graph - screen 17, the remaining 3 read routes (project/impact
// already wired for screen 10). node_labels/neighbor_labels only exist
// because get_graph_trace's own Cypher was fixed this step - see
// KNOWN_LIMITATIONS.md's updated [B10] entry on the trace shape.
// ----------------------------------------------------------------------

export interface GraphStatusResponse {
  fresh: boolean;
  current_state_version: number;
  projected_state_version: number | null;
  last_projected_at: string | null;
}

export interface GraphContradictionRow {
  [key: string]: unknown;
}

export interface GraphTraceResponse {
  n: Record<string, unknown> | null;
  node_labels: string[];
  neighbors: {
    relationship: string;
    neighbor: Record<string, unknown>;
    neighbor_labels: string[];
  }[];
}

export function getGraphStatus(tenderId: string): Promise<GraphStatusResponse> {
  return apiFetch<GraphStatusResponse>(`/tenders/${tenderId}/graph/status`);
}

export function getGraphContradictions(tenderId: string): Promise<GraphContradictionRow[]> {
  return apiFetch<GraphContradictionRow[]>(`/tenders/${tenderId}/graph/contradictions`);
}

// Mirrors graph_projection/driver.py's TENDER_SCOPED_LABELS/SHARED_LABELS
// split - tender-scoped nodes are keyed by `id`, shared nodes by
// `knowledge_item_id` (graph_service.get_graph_trace's own docstring).
// Screen 17's click-to-expand needs to know which one to read off a
// clicked neighbor before it can re-query graph/trace for it.
const SHARED_GRAPH_LABELS = new Set(["Employee", "Certification", "Project", "Capability"]);

export function graphNodeIdFor(labels: string[], node: Record<string, unknown>): string | null {
  const isShared = labels.some((label) => SHARED_GRAPH_LABELS.has(label));
  const key = isShared ? "knowledge_item_id" : "id";
  const value = node[key];
  return typeof value === "string" ? value : null;
}

export function getGraphTrace(
  tenderId: string,
  nodeLabel: string,
  nodeId: string,
): Promise<GraphTraceResponse> {
  const params = new URLSearchParams({ node_label: nodeLabel, node_id: nodeId });
  return apiFetch<GraphTraceResponse>(`/tenders/${tenderId}/graph/trace?${params.toString()}`);
}
