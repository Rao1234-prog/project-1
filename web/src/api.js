/* Thin API client. The acting role is sent as X-Bedrock-Role on every request
   (the server boundary enforces authorization). Errors are thrown as ApiError
   with the parsed detail so the UI can surface *what went wrong and what to do*
   — a 409 carries the blocking findings, a 403 carries the required role. */
const BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : (detail?.error || "request failed"));
    this.status = status;
    this.detail = detail;
  }
}

async function req(method, path, { role, body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (role) headers["X-Bedrock-Role"] = role;
  let res;
  try {
    res = await fetch(BASE + path, {
      method, headers, body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new ApiError(0, `Cannot reach the API at ${BASE}. Is the backend running?`);
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? data ?? res.statusText);
  return data;
}

export const api = {
  listOrgs: () => req("GET", "/orgs"),
  balances: (org) => req("GET", `/orgs/${org}/balances`),
  entries: (org, limit = 100) => req("GET", `/orgs/${org}/entries?limit=${limit}`),
  trail: (org, id) => req("GET", `/orgs/${org}/entries/${id}/trail`),
  queue: (org) => req("GET", `/orgs/${org}/queue`),
  audit: (org) => req("GET", `/orgs/${org}/audit`),
  checklist: (org, period) => req("GET", `/orgs/${org}/close/checklist?period=${period}`),
  trialBalance: (org) => req("GET", `/orgs/${org}/trial-balance`),
  verifyChain: (org) => req("GET", `/orgs/${org}/chain/verify`),
  review: (org, txnId, role, body) =>
    req("POST", `/orgs/${org}/transactions/${txnId}/reviews`, { role, body }),
  approveReconciliation: (org, role, body) =>
    req("POST", `/orgs/${org}/reconciliations/approve`, { role, body }),
  approveClose: (org, role, body) =>
    req("POST", `/orgs/${org}/close/approve`, { role, body }),
};
