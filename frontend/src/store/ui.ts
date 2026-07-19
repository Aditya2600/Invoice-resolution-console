import { create } from "zustand";

import type { DecisionStatus, JobStatus } from "@/lib/types";

export type StatusFilter = "ALL" | JobStatus;
export type DecisionFilter = "ALL" | DecisionStatus | "NONE";

interface UiState {
  search: string;
  statusFilter: StatusFilter;
  decisionFilter: DecisionFilter;
  rawTextOpen: boolean;
  mobileNavOpen: boolean;
  setSearch: (search: string) => void;
  setStatusFilter: (statusFilter: StatusFilter) => void;
  setDecisionFilter: (decisionFilter: DecisionFilter) => void;
  setRawTextOpen: (rawTextOpen: boolean) => void;
  setMobileNavOpen: (mobileNavOpen: boolean) => void;
  resetFilters: () => void;
}

/** UI-only state. Server data lives in TanStack Query. */
export const useUiStore = create<UiState>((set) => ({
  search: "",
  statusFilter: "ALL",
  decisionFilter: "ALL",
  rawTextOpen: false,
  mobileNavOpen: false,
  setSearch: (search) => set({ search }),
  setStatusFilter: (statusFilter) => set({ statusFilter }),
  setDecisionFilter: (decisionFilter) => set({ decisionFilter }),
  setRawTextOpen: (rawTextOpen) => set({ rawTextOpen }),
  setMobileNavOpen: (mobileNavOpen) => set({ mobileNavOpen }),
  resetFilters: () => set({ search: "", statusFilter: "ALL", decisionFilter: "ALL" }),
}));
