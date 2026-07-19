import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Check, FileText, Sparkles, Upload, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { ProcessingPanel } from "@/components/ProcessingPanel";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { jobKeys, useImportPurchaseOrders, useSeedPurchaseOrders } from "@/hooks/queries";
import { ApiError, uploadInvoiceWithProgress } from "@/lib/api";
import { cn } from "@/lib/utils";

export function ProcessInvoice() {
  return (
    <div className="mx-auto max-w-7xl px-5 md:px-8 py-10">
      <div className="mono-label text-muted-foreground">PROCESS</div>
      <h1 className="mt-3 text-5xl md:text-6xl font-semibold tracking-tight leading-[0.95] max-w-3xl">
        Process invoices
      </h1>
      <div className="mt-10 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <PoMasterStage />
        <InvoicesStage />
      </div>
    </div>
  );
}

function PoMasterStage() {
  const importMutation = useImportPurchaseOrders();
  const seedMutation = useSeedPurchaseOrders();
  const inputRef = useRef<HTMLInputElement>(null);
  const [lastResult, setLastResult] = useState<string | null>(null);

  const handleFile = (file: File | undefined) => {
    if (!file) return;
    // The API rejects anything that is not a .csv, so fail here rather than round-trip.
    if (!file.name.toLowerCase().endsWith(".csv")) {
      toast.error("The PO master must be a .csv file.");
      return;
    }
    importMutation.mutate(file, {
      onSuccess: (result) => {
        setLastResult(`Imported ${result.imported} purchase orders.`);
        toast.success(result.message);
      },
      onError: (error) => toast.error(error instanceof ApiError ? error.message : "Import failed."),
    });
  };

  return (
    <section className="rounded-2xl border border-divider bg-background p-6 md:p-8">
      <div className="flex items-center gap-2 mono-label text-muted-foreground">
        <span className="inline-flex size-5 items-center justify-center rounded-full bg-foreground text-background text-[10px]">
          1
        </span>
        STAGE ONE · PO MASTER
      </div>
      <h2 className="mt-4 text-2xl font-semibold tracking-tight">Import purchase-order master</h2>

      <div
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          handleFile(event.dataTransfer.files?.[0]);
        }}
        className="mt-6 rounded-xl border border-dashed border-divider p-6 flex flex-col items-center text-center bg-panel/40"
      >
        <FileText className="size-8 text-foreground/60" aria-hidden />
        <div className="mt-3 font-medium">Drop a CSV or browse</div>
        <div className="mono-label text-muted-foreground mt-1">PURCHASE ORDERS · CSV ONLY</div>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          onChange={(event) => {
            handleFile(event.target.files?.[0]);
            event.target.value = "";
          }}
        />
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          <Button
            onClick={() => inputRef.current?.click()}
            className="rounded-full bg-foreground text-background"
            disabled={importMutation.isPending}
          >
            {importMutation.isPending ? "Importing…" : "Select CSV"}
          </Button>
          <Button
            variant="outline"
            className="rounded-full border-foreground/20"
            disabled={seedMutation.isPending}
            onClick={() =>
              seedMutation.mutate(undefined, {
                onSuccess: (result) => {
                  setLastResult(`Seeded ${result.imported} demo purchase orders.`);
                  toast.success(result.message);
                },
                onError: (error) => toast.error(error instanceof ApiError ? error.message : "Seed failed."),
              })
            }
          >
            <Sparkles className="size-4 mr-1" aria-hidden />
            {seedMutation.isPending ? "Seeding…" : "Seed demo POs"}
          </Button>
        </div>
        {lastResult && <div className="mt-4 mono-label text-success">{lastResult}</div>}
      </div>
    </section>
  );
}

type UploadState = "queued" | "uploading" | "completed" | "duplicate" | "failed";

interface FileItem {
  id: string;
  file: File;
  state: UploadState;
  progress: number;
  message?: string;
  jobId?: string;
}

function InvoicesStage() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<FileItem[]>([]);
  const [running, setRunning] = useState(false);
  const queryClient = useQueryClient();
  const reduce = useReducedMotion();
  const navigate = useNavigate();

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    const items: FileItem[] = [];
    for (const file of Array.from(list)) {
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        toast.error(`${file.name} is not a PDF.`);
        continue;
      }
      if (file.size === 0) {
        toast.error(`${file.name} is empty.`);
        continue;
      }
      items.push({
        id: `${file.name}-${file.size}-${Math.random().toString(36).slice(2, 8)}`,
        file,
        state: "queued",
        progress: 0,
      });
    }
    setFiles((previous) => [...previous, ...items]);
  };

  const patch = (id: string, changes: Partial<FileItem>) =>
    setFiles((previous) => previous.map((item) => (item.id === id ? { ...item, ...changes } : item)));

  /** The API takes one PDF per request, so a batch is uploaded strictly sequentially. */
  const startUploads = async () => {
    if (running) return;
    setRunning(true);
    for (const item of files) {
      if (item.state !== "queued" && item.state !== "failed") continue;
      patch(item.id, { state: "uploading", progress: 0, message: undefined });
      try {
        const response = await uploadInvoiceWithProgress(item.file, (percent) =>
          patch(item.id, { progress: percent }),
        );
        patch(item.id, {
          state: response.created ? "completed" : "duplicate",
          progress: 100,
          jobId: response.job.job_id,
          message: response.created ? undefined : response.message,
        });
      } catch (error) {
        patch(item.id, {
          state: "failed",
          message: error instanceof ApiError ? error.message : "Upload failed.",
        });
      }
      queryClient.invalidateQueries({ queryKey: jobKeys.all });
    }
    setRunning(false);
  };

  const completed = files.filter((file) => file.state === "completed").length;
  const pending = files.filter((file) => file.state === "queued" || file.state === "failed").length;

  return (
    <section className="rounded-2xl border border-divider bg-background p-6 md:p-8">
      <div className="flex items-center gap-2 mono-label text-muted-foreground">
        <span className="inline-flex size-5 items-center justify-center rounded-full bg-foreground text-background text-[10px]">
          2
        </span>
        STAGE TWO · INVOICES
      </div>
      <h2 className="mt-4 text-2xl font-semibold tracking-tight">Queue invoice PDFs</h2>

      <div
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          addFiles(event.dataTransfer.files);
        }}
        className="mt-6 rounded-xl border border-dashed border-divider p-6 flex flex-col items-center text-center bg-panel/40"
      >
        <Upload className="size-8 text-foreground/60" aria-hidden />
        <div className="mt-3 font-medium">Drop invoice PDFs</div>
        <div className="mono-label text-muted-foreground mt-1">MULTIPLE FILES · PDF ONLY</div>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          multiple
          className="hidden"
          onChange={(event) => {
            addFiles(event.target.files);
            event.target.value = "";
          }}
        />
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          <Button onClick={() => inputRef.current?.click()} variant="outline" className="rounded-full border-foreground/20">
            Add PDFs
          </Button>
          <Button
            onClick={startUploads}
            disabled={pending === 0 || running}
            className="rounded-full bg-foreground text-background"
          >
            {running ? "Uploading…" : `Start${pending ? ` (${pending})` : ""}`}
          </Button>
        </div>
      </div>

      {running && (
        <div className="mt-6">
          <ProcessingPanel />
        </div>
      )}

      {completed > 0 && !running && (
        <p className="mt-4 mono-label text-success">{completed} QUEUED SUCCESSFULLY</p>
      )}

      <ul className="mt-6 flex flex-col divide-y divide-divider border-y border-divider">
        <AnimatePresence initial={false}>
          {files.map((item) => (
            <motion.li
              key={item.id}
              layout={!reduce}
              initial={reduce ? false : { opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduce ? undefined : { opacity: 0 }}
              transition={{ duration: 0.18 }}
              className="py-4 flex items-center gap-4"
            >
              <FileText className="size-4 text-foreground/60 shrink-0" aria-hidden />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-3">
                  <div className="truncate font-medium">{item.file.name}</div>
                  <div className="mono-label text-muted-foreground shrink-0 tabular-nums">
                    {Math.max(1, Math.round(item.file.size / 1024))} KB
                  </div>
                </div>
                <div className="mt-2 flex items-center gap-3">
                  <StateBadge state={item.state} />
                  {(item.state === "uploading" || item.state === "completed") && (
                    <Progress
                      value={item.progress}
                      aria-label={`Upload progress for ${item.file.name}`}
                      className="h-1 flex-1"
                    />
                  )}
                  {item.jobId && (item.state === "completed" || item.state === "duplicate") && (
                    <button
                      onClick={() => navigate(`/runs/${item.jobId}`)}
                      className="mono-label underline underline-offset-4 hover:text-electric shrink-0"
                    >
                      OPEN RUN
                    </button>
                  )}
                  {(item.state === "queued" || item.state === "failed") && (
                    <button
                      onClick={() => setFiles((previous) => previous.filter((file) => file.id !== item.id))}
                      aria-label={`Remove ${item.file.name}`}
                      className="text-foreground/50 hover:text-foreground"
                    >
                      <X className="size-4" />
                    </button>
                  )}
                </div>

                {item.state === "duplicate" && (
                  <div className="mt-2 rounded-md bg-electric/8 border border-electric/25 p-3 text-sm text-foreground/80">
                    <div className="flex items-start gap-2">
                      <AlertCircle className="size-4 text-electric shrink-0 mt-0.5" aria-hidden />
                      <div>
                        <span className="text-electric font-medium">Duplicate file · </span>
                        {item.message}{" "}
                        {item.jobId && (
                          <Link to={`/runs/${item.jobId}`} className="underline underline-offset-4">
                            View the existing run
                          </Link>
                        )}
                      </div>
                    </div>
                  </div>
                )}

                {item.state === "failed" && (
                  <div className="mt-2 rounded-md bg-destructive/8 border border-destructive/25 p-3 text-sm text-foreground/80">
                    <div className="flex items-start gap-2">
                      <AlertCircle className="size-4 text-destructive shrink-0 mt-0.5" aria-hidden />
                      <div>
                        <span className="text-destructive font-medium">Upload failed · </span>
                        {item.message}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </motion.li>
          ))}
        </AnimatePresence>
        {files.length === 0 && <li className="py-10 text-center mono-label text-muted-foreground">NO FILES QUEUED</li>}
      </ul>
    </section>
  );
}

const STATE_BADGE: Record<UploadState, { label: string; className: string; icon?: React.ReactNode }> = {
  queued: { label: "QUEUED", className: "bg-panel text-foreground/70 border-divider" },
  uploading: { label: "UPLOADING", className: "bg-electric/10 text-electric border-electric/25" },
  completed: {
    label: "QUEUED FOR WORKER",
    className: "bg-success/12 text-success border-success/25",
    icon: <Check className="size-3" aria-hidden />,
  },
  duplicate: { label: "DUPLICATE", className: "bg-electric/10 text-electric border-electric/25" },
  failed: { label: "FAILED", className: "bg-destructive/10 text-destructive border-destructive/25" },
};

function StateBadge({ state }: { state: UploadState }) {
  const badge = STATE_BADGE[state];
  return (
    <span
      className={cn(
        "mono-label inline-flex items-center gap-1 h-6 px-2 rounded-full border shrink-0",
        badge.className,
      )}
    >
      {badge.icon}
      {badge.label}
    </span>
  );
}
