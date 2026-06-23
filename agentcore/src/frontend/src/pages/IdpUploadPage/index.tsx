import type React from "react";
import { useState, useRef, useEffect, Fragment } from "react";
import { useNavigate } from "react-router-dom";
import {
  Upload,
  FileText,
  CheckCircle2,
  AlertCircle,
  Clock,
  MinusCircle,
  ArrowRight,
  RefreshCw,
  Inbox,
  ListTree,
} from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import Loading from "@/components/ui/loading";
import { cn } from "@/utils/utils";
import { ProcessingTrace } from "@/components/core/idpProcessingTrace";
import { api } from "@/controllers/API/api";
import { getURL } from "@/controllers/API/helpers/constants";
import { useGetRegistry } from "@/controllers/API/queries/registry/use-get-registry";
import { useUploadAndProcess } from "@/controllers/API/queries/idp/use-upload-and-process";

// ─── Types ────────────────────────────────────────────────────────────────────

type Env = "UAT" | "PROD";
type ProcessingState = "idle" | "uploading" | "processing" | "error";

interface SessionDoc {
  id: string;
  filename: string;
  agentName: string;
  predictedType: string | null;
  confidence: number | null;
  status: string;
  processedAt: string;
}

const IDP_ACCEPT = ".pdf,.png,.jpg,.jpeg,.tiff,.tif,.bmp,.xlsx,.xls,.docx,.doc";
const MAX_POLLS = 240;

// ─── Status chip ──────────────────────────────────────────────────────────────

function StatusChip({ status }: { status: string }) {
  if (status === "auto_approved")
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 text-[11px] font-medium text-emerald-600 dark:text-emerald-400 whitespace-nowrap">
        <CheckCircle2 className="h-3 w-3" /> Auto-Approved
      </span>
    );
  if (status === "reviewed")
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-blue-500/10 border border-blue-500/20 px-2 py-0.5 text-[11px] font-medium text-blue-600 dark:text-blue-400 whitespace-nowrap">
        <CheckCircle2 className="h-3 w-3" /> Reviewed
      </span>
    );
  if (status === "skipped")
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-muted border border-border px-2 py-0.5 text-[11px] font-medium text-muted-foreground whitespace-nowrap">
        <MinusCircle className="h-3 w-3" /> Skipped
      </span>
    );
  if (status === "failed")
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-destructive/10 border border-destructive/20 px-2 py-0.5 text-[11px] font-medium text-destructive whitespace-nowrap">
        <AlertCircle className="h-3 w-3" /> Failed
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-400/10 border border-amber-400/20 px-2 py-0.5 text-[11px] font-medium text-amber-600 dark:text-amber-400 whitespace-nowrap">
      <Clock className="h-3 w-3" /> Pending Review
    </span>
  );
}

function ConfBadge({ score }: { score: number }) {
  const cls =
    score >= 80
      ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
      : score >= 50
        ? "bg-amber-400/10 text-amber-600 dark:text-amber-400 border-amber-400/20"
        : "bg-destructive/10 text-destructive border-destructive/20";
  return (
    <span className={cn("inline-block rounded-full border px-2 py-0.5 text-[11px] font-semibold tabular-nums", cls)}>
      {score}%
    </span>
  );
}

// ─── Results panel (right side) ───────────────────────────────────────────────

function ResultsPanel({
  docs,
  env,
  onOpen,
}: {
  docs: SessionDoc[];
  env: Env;
  onOpen: (id: string) => void;
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  if (docs.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 text-center px-8">
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-muted/40">
          <Inbox className="h-8 w-8 text-muted-foreground/30" />
        </div>
        <div>
          <p className="text-sm font-semibold text-muted-foreground">No documents processed yet</p>
          <p className="text-xs text-muted-foreground/60 mt-1">
            Select an {env} agent, drop a document, and hit Process.
            <br />Results will appear here.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Panel header */}
      <div className="flex-shrink-0 flex items-center justify-between px-6 py-4 border-b bg-muted/5">
        <div>
          <h2 className="text-sm font-semibold">Session Results</h2>
          <p className="text-[11px] text-muted-foreground mt-0.5">
            Click any row to open the full extraction
          </p>
        </div>
        <span className="rounded-full bg-[#D04A02]/10 px-2.5 py-1 text-xs font-bold text-[#D04A02]">
          {docs.length} processed
        </span>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/20 hover:bg-muted/20">
              <TableHead className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/60 pl-6">Document</TableHead>
              <TableHead className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/60">Agent</TableHead>
              <TableHead className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/60">Type</TableHead>
              <TableHead className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/60 text-center">Confidence</TableHead>
              <TableHead className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/60">Status</TableHead>
              <TableHead className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/60">Time</TableHead>
              <TableHead className="w-12 pr-6" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {docs.map((doc) => (
              <Fragment key={doc.id}>
              <TableRow
                className="cursor-pointer group hover:bg-muted/20 transition-colors"
                onClick={() => onOpen(doc.id)}
              >
                <TableCell className="pl-6">
                  <div className="flex items-center gap-2.5">
                    <div className="h-8 w-8 rounded-lg bg-[#D04A02]/8 flex items-center justify-center flex-shrink-0">
                      <FileText className="h-4 w-4 text-[#D04A02]/60" />
                    </div>
                    <span className="max-w-[200px] truncate text-sm font-medium">{doc.filename}</span>
                  </div>
                </TableCell>
                <TableCell>
                  <span className="max-w-[140px] truncate block text-xs text-muted-foreground">
                    {doc.agentName}
                  </span>
                </TableCell>
                <TableCell>
                  {doc.predictedType ? (
                    <span className="rounded-md border border-border bg-muted/40 px-2 py-0.5 text-[11px] font-medium whitespace-nowrap">
                      {doc.predictedType}
                    </span>
                  ) : (
                    <span className="text-muted-foreground text-xs">—</span>
                  )}
                </TableCell>
                <TableCell className="text-center">
                  {doc.confidence != null ? (
                    <ConfBadge score={Math.round(doc.confidence * 100)} />
                  ) : (
                    <span className="text-muted-foreground text-xs">—</span>
                  )}
                </TableCell>
                <TableCell>
                  <StatusChip status={doc.status} />
                </TableCell>
                <TableCell className="text-xs text-muted-foreground tabular-nums whitespace-nowrap">
                  {doc.processedAt}
                </TableCell>
                <TableCell className="pr-6">
                  <div className="flex items-center justify-end gap-0.5">
                    <button
                      className={cn(
                        "flex items-center justify-center rounded-lg p-1.5 hover:bg-[#D04A02]/10 hover:text-[#D04A02] transition-colors",
                        expandedId === doc.id ? "text-[#D04A02]" : "text-muted-foreground opacity-0 group-hover:opacity-100",
                      )}
                      onClick={(e) => { e.stopPropagation(); setExpandedId(expandedId === doc.id ? null : doc.id); }}
                      title="Show processing trace"
                    >
                      <ListTree className="h-4 w-4" />
                    </button>
                    <button
                      className="opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center rounded-lg p-1.5 hover:bg-[#D04A02]/10 text-muted-foreground hover:text-[#D04A02]"
                      onClick={(e) => { e.stopPropagation(); onOpen(doc.id); }}
                      title="Open in Processed Docs"
                    >
                      <ArrowRight className="h-4 w-4" />
                    </button>
                  </div>
                </TableCell>
              </TableRow>
              {expandedId === doc.id && (
                <TableRow className="hover:bg-transparent">
                  <TableCell colSpan={7} className="p-0 bg-muted/10 border-t">
                    <p className="px-4 pt-3 text-[10px] font-bold uppercase tracking-widest text-muted-foreground/60">Processing Trace</p>
                    <ProcessingTrace docId={doc.id} />
                  </TableCell>
                </TableRow>
              )}
              </Fragment>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

// ─── Upload panel (left side) ─────────────────────────────────────────────────

function UploadPanel({
  env,
  onDocProcessed,
}: {
  env: Env;
  onDocProcessed: (doc: SessionDoc) => void;
}) {
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [state, setState] = useState<ProcessingState>("idle");
  const [errorMsg, setErrorMsg] = useState("");
  const [isDragging, setIsDragging] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollCountRef = useRef(0);

  const { data: registryData, isLoading } = useGetRegistry(
    { deployment_env: env, page_size: 100 },
    { staleTime: 60_000 },
  );
  const agents = registryData?.items ?? [];
  const selectedAgent = agents.find((a) => a.agent_id === selectedAgentId);

  const { run: uploadAndProcess } = useUploadAndProcess();

  const stopPolling = () => {
    if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
  };

  useEffect(() => () => stopPolling(), []);

  const pollForResult = async (documentId: string, filename: string) => {
    pollCountRef.current += 1;
    if (pollCountRef.current > MAX_POLLS) {
      setState("error");
      setErrorMsg("Processing timed out — check backend logs for details");
      return;
    }
    try {
      const res = await api.get(`${getURL("IDP_PROCESSED_DOCS")}/${documentId}`);
      const doc = res.data;
      const terminal = ["extracted", "pending_review", "auto_approved", "reviewed", "failed", "skipped", "split"];
      if (terminal.includes(doc.status)) {
        onDocProcessed({
          id: documentId,
          filename,
          agentName: selectedAgent?.title ?? "Unknown Agent",
          predictedType: doc.predicted_type ?? null,
          confidence: doc.overall_confidence ?? null,
          status: doc.status,
          processedAt: new Date().toLocaleTimeString([], {
            hour: "2-digit", minute: "2-digit", second: "2-digit",
          }),
        });
        setFile(null);
        if (doc.status === "failed") {
          setState("error");
          setErrorMsg(doc.error_message || "Document processing failed");
        } else if (doc.status === "skipped") {
          setState("error");
          setErrorMsg("Document type not in configured types — document was skipped");
        } else {
          setState("idle");
        }
        return;
      }
      pollRef.current = setTimeout(() => pollForResult(documentId, filename), 2500);
    } catch (e: any) {
      setState("error");
      setErrorMsg(e?.message || "Failed to retrieve processing result");
    }
  };

  const handleRun = async () => {
    if (!file || !selectedAgentId) return;
    const filename = file.name;
    try {
      stopPolling();
      setState("uploading");
      setErrorMsg("");
      pollCountRef.current = 0;
      const processResult = await uploadAndProcess(selectedAgentId, file);
      setState("processing");
      pollForResult(String(processResult.document_id), filename);
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || "Upload failed";
      setState("error");
      setErrorMsg(msg);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) { setFile(f); e.target.value = ""; setState("idle"); setErrorMsg(""); }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    const f = e.dataTransfer.files?.[0];
    if (f) { setFile(f); setState("idle"); setErrorMsg(""); }
  };

  const isRunning = state === "uploading" || state === "processing";
  const canProcess = !!file && !!selectedAgentId && !isRunning;

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      {/* Panel header */}
      <div className="flex-shrink-0 flex items-center gap-3 border-b bg-muted/5 px-6 py-4">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#D04A02]/10 flex-shrink-0">
          <Upload className="h-4 w-4 text-[#D04A02]" />
        </div>
        <div className="min-w-0">
          <p className="text-sm font-semibold">Upload Document</p>
          <p className="text-[11px] text-muted-foreground truncate">
            {isRunning
              ? state === "uploading" ? "Uploading your file…" : "AI extraction in progress…"
              : `Select an agent, then drop a file`}
          </p>
        </div>
        {isRunning && <Loading className="h-4 w-4 text-[#D04A02] ml-auto flex-shrink-0" />}
      </div>

      <div className="flex-1 px-6 py-6 space-y-6">

        {/* Step 1 — Agent */}
        <div className="space-y-2">
          <div className="flex items-center gap-2 mb-2">
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[#D04A02]/10 text-[10px] font-bold text-[#D04A02]">
              1
            </span>
            <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/60">
              Select Agent
            </span>
          </div>
          <Select value={selectedAgentId} onValueChange={setSelectedAgentId} disabled={isRunning}>
            <SelectTrigger className="h-10 rounded-lg">
              <SelectValue
                placeholder={
                  isLoading ? "Loading agents…"
                  : agents.length === 0 ? `No ${env} agents published yet`
                  : "Choose a published agent"
                }
              />
            </SelectTrigger>
            <SelectContent>
              {agents.length === 0 ? (
                <div className="py-6 text-center text-sm text-muted-foreground">
                  No agents published to {env}
                </div>
              ) : (
                agents.map((a) => (
                  <SelectItem key={a.agent_id} value={a.agent_id}>
                    {a.title}
                  </SelectItem>
                ))
              )}
            </SelectContent>
          </Select>
          {selectedAgent && (
            <p className="text-[11px] text-muted-foreground pl-1">
              {selectedAgent.version_label ?? selectedAgent.version_number ?? "Latest version"}
              {selectedAgent.organization_name ? ` · ${selectedAgent.organization_name}` : ""}
            </p>
          )}
        </div>

        {/* Step 2 — File drop zone */}
        <div className="space-y-2">
          <div className="flex items-center gap-2 mb-2">
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[#D04A02]/10 text-[10px] font-bold text-[#D04A02]">
              2
            </span>
            <span className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/60">
              Drop Document
            </span>
          </div>

          <div
            className={cn(
              "relative flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed transition-all duration-200",
              "min-h-[220px] text-center px-6 py-8",
              isDragging
                ? "border-[#D04A02] bg-[#D04A02]/[0.04] scale-[1.005] cursor-copy"
                : file
                  ? "border-[#D04A02]/40 bg-[#D04A02]/[0.02] cursor-pointer"
                  : "border-border/60 bg-muted/10 hover:border-[#D04A02]/30 hover:bg-muted/20 cursor-pointer",
              (!selectedAgentId || isRunning) && "pointer-events-none opacity-40",
            )}
            onClick={() => !isRunning && fileInputRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
          >
            {isRunning ? (
              <div className="flex flex-col items-center gap-3">
                <div className="relative flex h-14 w-14 items-center justify-center">
                  <div className="absolute inset-0 rounded-full border-2 border-[#D04A02]/20 animate-ping" />
                  <Loading className="h-7 w-7 text-[#D04A02]" />
                </div>
                <div>
                  <p className="text-sm font-semibold">
                    {state === "uploading" ? "Uploading…" : "Processing document…"}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground max-w-[220px] truncate">{file?.name}</p>
                </div>
              </div>
            ) : file ? (
              <div className="flex flex-col items-center gap-2.5">
                <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-[#D04A02]/10">
                  <FileText className="h-7 w-7 text-[#D04A02]" />
                </div>
                <div>
                  <p className="text-sm font-semibold max-w-[240px] truncate">{file.name}</p>
                  <p className="text-[11px] text-muted-foreground mt-0.5 tabular-nums">
                    {(file.size / 1024).toFixed(1)} KB · {file.type || "document"}
                  </p>
                </div>
                <button
                  className="text-xs text-muted-foreground hover:text-destructive transition-colors"
                  onClick={(e) => { e.stopPropagation(); setFile(null); setErrorMsg(""); }}
                >
                  Remove file
                </button>
              </div>
            ) : (
              <div className="flex flex-col items-center gap-3">
                <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-muted/50">
                  <Upload className="h-7 w-7 text-muted-foreground/40" />
                </div>
                <div>
                  <p className="text-sm font-medium">
                    {isDragging ? "Release to upload" : "Drop a document here"}
                  </p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    or{" "}
                    <span className="text-[#D04A02] font-semibold">click to browse</span>
                  </p>
                </div>
                <p className="text-[10px] text-muted-foreground/50 uppercase tracking-widest">
                  PDF · PNG · JPG · TIFF · Excel · Word
                </p>
              </div>
            )}
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept={IDP_ACCEPT}
            className="hidden"
            onChange={handleFileChange}
          />
        </div>

        {/* Error */}
        {state === "error" && errorMsg && (
          <div className="flex items-start gap-2.5 rounded-lg border border-destructive/20 bg-destructive/5 px-3.5 py-3">
            <AlertCircle className="h-4 w-4 text-destructive flex-shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <p className="text-xs font-semibold text-destructive">Processing error</p>
              <p className="text-xs text-destructive/80 mt-0.5 break-words">{errorMsg}</p>
            </div>
            <button
              className="flex-shrink-0 text-destructive/40 hover:text-destructive transition-colors"
              onClick={() => { setState("idle"); setErrorMsg(""); }}
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

        {/* Process button */}
        <Button
          className={cn(
            "w-full h-11 rounded-xl gap-2 font-semibold text-sm transition-all",
            canProcess
              ? "bg-[#D04A02] hover:bg-[#B84000] text-white shadow-sm"
              : "",
          )}
          variant={canProcess ? "default" : "secondary"}
          disabled={!canProcess}
          onClick={handleRun}
        >
          {isRunning ? (
            <>
              <Loading className="h-4 w-4" />
              {state === "uploading" ? "Uploading…" : "Processing…"}
            </>
          ) : (
            <>
              <Upload className="h-4 w-4" />
              Process Document
            </>
          )}
        </Button>

      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function IdpUploadPage() {
  const navigate = useNavigate();
  const [env, setEnv] = useState<Env>("UAT");
  const [sessionDocs, setSessionDocs] = useState<Record<Env, SessionDoc[]>>({
    UAT: [],
    PROD: [],
  });

  const addDoc = (doc: SessionDoc) =>
    setSessionDocs((prev) => ({ ...prev, [env]: [doc, ...prev[env]] }));

  const openDoc = (id: string) => navigate(`/processed-docs?doc=${id}`);

  return (
    <div className="flex flex-col h-full bg-background overflow-hidden">

      {/* ── Page header ── */}
      <div className="flex-shrink-0 border-b bg-muted/5">
        <div className="flex items-end justify-between px-6 pt-5 pb-0">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Document Upload</h1>
            <p className="text-sm text-muted-foreground mt-0.5 mb-3">
              Upload documents to a running IDP agent for extraction and processing.
            </p>
          </div>
          {/* Session doc count across both envs */}
          {(sessionDocs.UAT.length + sessionDocs.PROD.length) > 0 && (
            <div className="mb-3 text-xs text-muted-foreground">
              {sessionDocs.UAT.length + sessionDocs.PROD.length} processed this session
            </div>
          )}
        </div>

        {/* Env tabs */}
        <div className="flex gap-0 px-6">
          {(["UAT", "PROD"] as const).map((e) => (
            <button
              key={e}
              onClick={() => setEnv(e)}
              className={cn(
                "relative flex items-center gap-2 px-5 h-10 border-b-2 text-sm font-semibold transition-all",
                env === e
                  ? "border-[#D04A02] text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:border-border",
              )}
            >
              {e}
              <span
                className={cn(
                  "rounded px-1.5 py-0.5 text-[10px] font-bold",
                  e === "UAT"
                    ? "bg-amber-400/15 text-amber-600 dark:text-amber-400"
                    : "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
                )}
              >
                {e === "UAT" ? "Testing" : "Live"}
              </span>
              {sessionDocs[e].length > 0 && (
                <span className="ml-0.5 rounded-full bg-[#D04A02]/15 px-1.5 py-0.5 text-[10px] font-bold text-[#D04A02]">
                  {sessionDocs[e].length}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* ── Two-column body ── */}
      <div className="flex flex-1 overflow-hidden">

        {/* Left: upload controls */}
        <div className="flex-shrink-0 w-[400px] border-r overflow-hidden">
          <UploadPanel key={env} env={env} onDocProcessed={addDoc} />
        </div>

        {/* Right: results */}
        <div className="flex-1 overflow-hidden">
          <ResultsPanel docs={sessionDocs[env]} env={env} onOpen={openDoc} />
        </div>

      </div>
    </div>
  );
}
