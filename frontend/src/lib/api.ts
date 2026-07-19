import type {
  ImportPurchaseOrdersResponse,
  AuthenticatedActor,
  JobDetail,
  JobListItem,
  OpsOverview,
  PurchaseOrder,
  RetryJobRequest,
  RetryJobResponse,
  ResolveReviewRequest,
  ResolveReviewResponse,
  UploadInvoiceResponse,
} from "./types";

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function authorizationHeaders(): Record<string, string> {
  const configured = import.meta.env.VITE_API_TOKEN as string | undefined;
  const stored = typeof window === "undefined" ? null : window.sessionStorage.getItem("invoice_api_token");
  const token = stored || configured;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function withAuthentication(init?: RequestInit): RequestInit {
  const headers = new Headers(init?.headers);
  for (const [key, value] of Object.entries(authorizationHeaders())) headers.set(key, value);
  return { ...init, headers };
}

function errorMessage(status: number, detail?: string): string {
  if (detail) return detail;
  if (status === 401) return "Authentication is required. Provide a valid bearer token.";
  if (status === 403) return "Your account does not have permission for this action.";
  if (status === 413) return "The upload exceeds the server's size limit.";
  if (status === 422) return "The submitted file or form could not be accepted.";
  return `Request failed (${status}).`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, withAuthentication(init));
  } catch {
    throw new ApiError("Cannot reach the API. Start the FastAPI server or Docker Compose.", 0);
  }

  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && typeof body.detail === "string" ? body.detail : undefined;
    throw new ApiError(errorMessage(response.status, detail), response.status);
  }
  return body as T;
}

function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  return request<T>(path, { method: "POST", body: form });
}

/**
 * fetch() cannot report request-body progress, so invoice upload uses XHR.
 * The API accepts exactly one PDF per call; callers upload batches sequentially.
 */
export function uploadInvoiceWithProgress(
  file: File,
  onProgress: (percent: number) => void,
  signal?: AbortSignal,
): Promise<UploadInvoiceResponse> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE_URL}/invoices/upload`);
    for (const [key, value] of Object.entries(authorizationHeaders())) xhr.setRequestHeader(key, value);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        body = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(100);
        resolve(body as UploadInvoiceResponse);
        return;
      }
      const detail =
        body && typeof body === "object" && typeof (body as { detail?: unknown }).detail === "string"
          ? (body as { detail: string }).detail
          : undefined;
      reject(new ApiError(errorMessage(xhr.status, detail), xhr.status));
    };
    xhr.onerror = () =>
      reject(new ApiError("Cannot reach the API. Start the FastAPI server or Docker Compose.", 0));
    xhr.onabort = () => reject(new ApiError("Upload cancelled.", 0));
    signal?.addEventListener("abort", () => xhr.abort());

    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}

export const api = {
  currentActor: () => request<AuthenticatedActor>("/auth/me"),
  opsOverview: (windowHours = 24) => request<OpsOverview>(`/ops/overview?window_hours=${windowHours}`),
  listJobs: (limit = 200) => request<{ jobs: JobListItem[] }>(`/jobs?limit=${limit}`).then((body) => body.jobs),
  getJob: (jobId: string) => request<JobDetail>(`/jobs/${jobId}`),
  reviewCandidates: (jobId: string) =>
    request<{ candidates: PurchaseOrder[] }>(`/jobs/${jobId}/review/candidates`).then((body) => body.candidates),
  resolveReview: (jobId: string, body: ResolveReviewRequest) =>
    request<ResolveReviewResponse>(`/jobs/${jobId}/review/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  retryJob: (jobId: string, body: RetryJobRequest) =>
    request<RetryJobResponse>(`/jobs/${jobId}/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  uploadInvoice: (file: File) => upload<UploadInvoiceResponse>("/invoices/upload", file),
  importPurchaseOrders: (file: File) => upload<ImportPurchaseOrdersResponse>("/purchase-orders/import", file),
  seedPurchaseOrders: () =>
    request<ImportPurchaseOrdersResponse>("/demo/seed-purchase-orders", { method: "POST" }),
  resetDemo: () => request<{ message: string }>("/demo/reset", { method: "POST" }),
  openDocument: async (documentId: string) => {
    let response: Response;
    try {
      response = await fetch(
        `${API_BASE_URL}/documents/${documentId}/file`,
        withAuthentication({ cache: "no-store" }),
      );
    } catch {
      throw new ApiError("Cannot reach the API. Start the FastAPI server or Docker Compose.", 0);
    }
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const detail = body && typeof body.detail === "string" ? body.detail : undefined;
      throw new ApiError(errorMessage(response.status, detail), response.status);
    }
    const url = URL.createObjectURL(await response.blob());
    window.open(url, "_blank", "noopener,noreferrer");
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  },
};
