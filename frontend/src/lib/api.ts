import type {
  ImportPurchaseOrdersResponse,
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, init);
  } catch {
    throw new ApiError("Cannot reach the API. Start the FastAPI server or Docker Compose.", 0);
  }

  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && typeof body.detail === "string" ? body.detail : `Request failed (${response.status}).`;
    throw new ApiError(detail, response.status);
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
          : `Upload failed (${xhr.status}).`;
      reject(new ApiError(detail, xhr.status));
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
  documentFileUrl: (documentId: string) => `${API_BASE_URL}/documents/${documentId}/file`,
};
