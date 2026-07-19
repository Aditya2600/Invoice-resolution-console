import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { isRunning } from "@/lib/format";
import type { JobDetail, JobListItem, PurchaseOrder, ResolveReviewRequest, RetryJobRequest } from "@/lib/types";

const POLL_MS = 2500;

export const jobKeys = {
  all: ["jobs"] as const,
  list: (limit: number) => ["jobs", "list", limit] as const,
  detail: (jobId: string) => ["jobs", "detail", jobId] as const,
};

/** Polls only while at least one run is still PENDING or PROCESSING. */
export function useJobs(limit = 200) {
  return useQuery<JobListItem[]>({
    queryKey: jobKeys.list(limit),
    queryFn: () => api.listJobs(limit),
    refetchInterval: (query) => (query.state.data?.some((job) => isRunning(job.status)) ? POLL_MS : false),
  });
}

export function useJob(jobId?: string) {
  return useQuery<JobDetail>({
    queryKey: jobKeys.detail(jobId ?? ""),
    queryFn: () => api.getJob(jobId as string),
    enabled: Boolean(jobId),
    refetchInterval: (query) => (query.state.data && isRunning(query.state.data.job.status) ? POLL_MS : false),
  });
}

/** Open POs a reviewer may pick, with live balances. Only fetched while the job awaits review. */
export function useReviewCandidates(jobId: string | undefined, enabled: boolean) {
  return useQuery<PurchaseOrder[]>({
    queryKey: [...jobKeys.detail(jobId ?? ""), "candidates"],
    queryFn: () => api.reviewCandidates(jobId as string),
    enabled: Boolean(jobId) && enabled,
  });
}

export function useResolveReview(jobId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ResolveReviewRequest) => api.resolveReview(jobId, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: jobKeys.all }),
  });
}

/** Re-queues a failed run; invalidating jobKeys.all refreshes both this run and the dashboard. */
export function useRetryJob(jobId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: RetryJobRequest) => api.retryJob(jobId, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: jobKeys.all }),
  });
}

export function useUploadInvoice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api.uploadInvoice(file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: jobKeys.all }),
  });
}

export function useImportPurchaseOrders() {
  return useMutation({ mutationFn: (file: File) => api.importPurchaseOrders(file) });
}

export function useSeedPurchaseOrders() {
  return useMutation({ mutationFn: () => api.seedPurchaseOrders() });
}
