import {
  Search,
  Download,
  Eye,
  ChevronLeft,
  Maximize2,
  FileText,
  CheckCircle2,
  Clock,
  AlertCircle,
  MinusCircle,
  X,
  RotateCcw,
  Info,
  ListTree,
  Save,
  GitCompare,
} from "lucide-react";
import { useState, useMemo, useEffect, useRef } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/utils/utils";
import {
  useGetProcessedDocs,
  useGetProcessedDoc,
  usePatchProcessedDocFields,
  usePostProcessedDocReview,
  type ProcessedDoc as ApiProcessedDoc,
  type ProcessedDocDetail,
  type FieldUpdateItem,
  useGetMatchResults,
  useTriggerMatch,
} from "@/controllers/API/queries/idp";
import { api } from "@/controllers/API/api";
import { ProcessingTrace } from "@/components/core/idpProcessingTrace";
import { MatchResultsPanel } from "./components/MatchResultsPanel";
import { RunMatchModal } from "./components/RunMatchModal";

// ─── Types ────────────────────────────────────────────────────────────────────

type DocStatus = "pending" | "auto_approved" | "reviewed" | "skipped";

interface ExtractedField {
  key: string;
  value: string;
  confidence: number;
  changed?: boolean;
  fieldId?: string;   // idp_extracted_headers.id — needed to PATCH the reviewed value
  reasoning?: string | null;                       // model's evidence/why for this value
  source?: Record<string, unknown> | null;         // {page_number, bounding_box} where it was found
}

interface LineItem {
  id: string;
  [col: string]: string;
}

interface ProcessedDoc {
  id: string;
  name: string;
  sourceAgent: string;
  dateProcessed: string;
  documentType: string;
  fileType: string;
  overallConfidence: number;
  status: DocStatus;
  headerFields: ExtractedField[];
  lineItems: LineItem[];
  lineItemColumns: string[];
  reviewer?: string;
  reviewedAt?: string;
  reviewDraft?: boolean; // reviewer saved edits as a draft (pending, not yet submitted)
  // rowId -> (columnName -> idp_extracted_line_items.id), so edited cells can be PATCHed.
  cellIds?: Record<string, Record<string, string>>;
}

// ─── API → page adapters ──────────────────────────────────────────────────────

function mapApiStatus(s: string): DocStatus {
  if (s === "auto_approved") return "auto_approved";
  if (s === "reviewed") return "reviewed";
  if (s === "skipped") return "skipped";
  return "pending"; // pending_review, extracted, queued, processing, failed
}

function listDocToPage(d: ApiProcessedDoc): ProcessedDoc {
  return {
    id: d.id,
    name: d.original_filename,
    sourceAgent: d.agent_name ?? d.agent_id.slice(0, 8),
    dateProcessed: (d.processing_completed_at ?? d.created_at ?? "").slice(0, 19).replace("T", " "),
    documentType: d.predicted_type ?? d.file_type ?? "—",
    fileType: (d.file_type ?? "").toLowerCase().replace(/^\./, ""),
    overallConfidence: Math.round((d.overall_confidence ?? 0) * 100),
    status: mapApiStatus(d.status),
    reviewDraft: d.review_draft ?? false,
    headerFields: [],
    lineItems: [],
    lineItemColumns: [],
  };
}

function enrichWithDetail(
  base: ProcessedDoc | undefined,
  detail?: ProcessedDocDetail,
): ProcessedDoc | null {
  if (!base) return null;
  if (!detail) return base;
  const headerFields: ExtractedField[] = detail.headers.map((h) => ({
    key: h.field_name,
    value: h.reviewed_value ?? h.extracted_value ?? "",
    confidence: Math.round((h.confidence_score ?? 0) * 100),
    fieldId: h.id,
    reasoning: h.reasoning_trace,
    source: h.source_location,
  }));
  const cols = Array.from(new Set(detail.line_items.map((li) => li.column_name)));
  const rows: Record<number, LineItem> = {};
  const cellIds: Record<string, Record<string, string>> = {};
  detail.line_items.forEach((li) => {
    const rowId = String(li.row_index);
    rows[li.row_index] = rows[li.row_index] ?? ({ id: rowId } as LineItem);
    rows[li.row_index][li.column_name] = li.reviewed_value ?? li.extracted_value ?? "";
    (cellIds[rowId] = cellIds[rowId] ?? {})[li.column_name] = li.id;
  });
  return {
    ...base,
    reviewDraft: detail.review_draft ?? base.reviewDraft,
    headerFields,
    lineItems: Object.values(rows),
    lineItemColumns: cols,
    cellIds,
  };
}

// ─── CSV export (client-side; no backend export endpoint) ──────────────────────

function _csvEscape(v: unknown): string {
  return `"${String(v ?? "").replace(/"/g, '""')}"`;
}

/** Download a summary CSV of the given processed docs (filename, agent, date, type, confidence, status). */
function exportDocsToCsv(rows: ProcessedDoc[], filename: string): void {
  const header = ["Document", "Agent", "Date", "Type", "Confidence", "Status"];
  const lines = [header.join(",")];
  rows.forEach((d) => {
    lines.push(
      [d.name, d.sourceAgent, d.dateProcessed, d.documentType, `${d.overallConfidence}%`, d.status]
        .map(_csvEscape)
        .join(","),
    );
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Download the per-document processing-log artifact (text flow.log) via the authenticated API. */
async function downloadDocLog(doc: ProcessedDoc): Promise<void> {
  try {
    const res = await api.get(`/api/v1/idp/documents/${doc.id}/log/download`, { responseType: "blob" });
    const url = URL.createObjectURL(res.data as Blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${doc.name || "document"}_processing_log.txt`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    console.error("[ProcessedDocs] processing-log download failed", e);
  }
}

/** Build the reasoning + source-location tooltip text for a field (audit/evidence trail). */
function evidenceTitle(f: ExtractedField): string {
  const parts: string[] = [];
  if (f.reasoning) parts.push(`Why: ${f.reasoning}`);
  const src = f.source as { page_number?: number; bounding_box?: unknown } | null | undefined;
  if (src?.page_number != null) {
    const bbox = Array.isArray(src.bounding_box) ? "  ·  located on page" : "";
    parts.push(`Source: page ${src.page_number}${bbox}`);
  }
  return parts.join("\n");
}

// ─── Seed data ────────────────────────────────────────────────────────────────

const SEED_DOCS: ProcessedDoc[] = [
  {
    id: "1",
    name: "INV-2026-00142.pdf",
    sourceAgent: "Invoice Processor",
    dateProcessed: "2026-06-04 09:12",
    documentType: "Invoice",
    fileType: "pdf",
    overallConfidence: 92,
    status: "auto_approved",
    headerFields: [
      { key: "invoice_number", value: "INV-2026-00142",    confidence: 98 },
      { key: "invoice_date",   value: "2026-05-30",        confidence: 96 },
      { key: "vendor_name",    value: "Acme Supplies Ltd", confidence: 91 },
      { key: "total_amount",   value: "14,200.00",         confidence: 88 },
      { key: "tax_amount",     value: "2,420.00",          confidence: 85 },
    ],
    lineItemColumns: ["description", "qty", "unit_price", "total"],
    lineItems: [
      { id: "r1", description: "Widget A",    qty: "10", unit_price: "500.00",   total: "5,000.00"  },
      { id: "r2", description: "Widget B",    qty: "5",  unit_price: "800.00",   total: "4,000.00"  },
      { id: "r3", description: "Service Fee", qty: "1",  unit_price: "2,780.00", total: "2,780.00"  },
    ],
  },
  {
    id: "2",
    name: "PAN_CARD_scan.png",
    sourceAgent: "KYC Extractor",
    dateProcessed: "2026-06-04 09:45",
    documentType: "PAN Card",
    fileType: "png",
    overallConfidence: 61,
    status: "pending",
    headerFields: [
      { key: "pan_number",    value: "ABCDE1234F",   confidence: 72 },
      { key: "full_name",     value: "RAMESH KUMAR", confidence: 65 },
      { key: "date_of_birth", value: "15/03/1985",   confidence: 54 },
      { key: "father_name",   value: "",              confidence: 22 },
    ],
    lineItemColumns: [],
    lineItems: [],
  },
  {
    id: "3",
    name: "receipt_20260603.jpg",
    sourceAgent: "Receipt Scanner",
    dateProcessed: "2026-06-03 17:20",
    documentType: "Receipt",
    fileType: "jpg",
    overallConfidence: 88,
    status: "reviewed",
    reviewer: "jane.doe@pwc.com",
    reviewedAt: "2026-06-03 18:05",
    headerFields: [
      { key: "merchant_name",     value: "Starbucks",  confidence: 95 },
      { key: "transaction_date",  value: "2026-06-03", confidence: 90 },
      { key: "total_amount",      value: "340.50",     confidence: 85 },
    ],
    lineItemColumns: ["item", "price"],
    lineItems: [
      { id: "r1", item: "Caramel Macchiato", price: "320.00" },
      { id: "r2", item: "Cookie",            price: "20.50"  },
    ],
  },
];

// ─── Confidence helpers ───────────────────────────────────────────────────────

function confColor(score: number) {
  if (score >= 80) return { dot: "bg-emerald-500", text: "text-emerald-600 dark:text-emerald-400", badge: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20" };
  if (score >= 50) return { dot: "bg-amber-400",   text: "text-amber-600  dark:text-amber-400",  badge: "bg-amber-400/10  text-amber-600  dark:text-amber-400  border-amber-400/20"  };
  return               { dot: "bg-red-500",        text: "text-red-600    dark:text-red-400",    badge: "bg-red-500/10    text-red-600    dark:text-red-400    border-red-500/20"    };
}

function ConfidencePill({ score }: { score: number }) {
  const c = confColor(score);
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold tabular-nums", c.badge)}>
      <span className={cn("h-1.5 w-1.5 rounded-full", c.dot)} />
      {score}%
    </span>
  );
}

function StatusChip({ status }: { status: DocStatus }) {
  if (status === "auto_approved")
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-0.5 text-xs font-medium text-emerald-600 dark:text-emerald-400">
        <CheckCircle2 className="h-3 w-3" /> Auto-Approved
      </span>
    );
  if (status === "reviewed")
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-500/10 border border-blue-500/20 px-2.5 py-0.5 text-xs font-medium text-blue-600 dark:text-blue-400">
        <CheckCircle2 className="h-3 w-3" /> Reviewed
      </span>
    );
  if (status === "skipped")
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-muted border border-border px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
        <MinusCircle className="h-3 w-3" /> Skipped
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-400/10 border border-amber-400/20 px-2.5 py-0.5 text-xs font-medium text-amber-600 dark:text-amber-400">
      <Clock className="h-3 w-3" /> Pending Review
    </span>
  );
}

// ─── Document Viewer ──────────────────────────────────────────────────────────

const IMAGE_TYPES = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "tif"]);
const PDF_TYPES   = new Set(["pdf"]);

function DocumentViewer({ doc }: { doc: ProcessedDoc }) {
  const ft = doc.fileType.toLowerCase();
  const isSeedDoc = ["1", "2", "3"].includes(doc.id);

  // Fetch the file via the authenticated axios client to avoid credential errors
  // when using a raw <iframe src> or <img src> (those requests don't carry auth headers).
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [fetchError, setFetchError] = useState(false);

  useEffect(() => {
    if (isSeedDoc) return;
    let revoked = false;
    setFetchError(false);
    setBlobUrl(null);
    api
      .get(`/api/v1/idp/processed-docs/${doc.id}/file`, { responseType: "blob" })
      .then((res) => {
        if (revoked) return;
        const url = URL.createObjectURL(res.data as Blob);
        setBlobUrl(url);
      })
      .catch(() => {
        if (!revoked) setFetchError(true);
      });
    return () => {
      revoked = true;
      setBlobUrl((prev) => { if (prev) URL.revokeObjectURL(prev); return null; });
    };
  }, [doc.id, isSeedDoc]);

  const handleDownload = () => {
    if (!blobUrl) return;
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = doc.name;
    a.click();
  };

  const toolbar = (
    <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/5 bg-[#111]/60 backdrop-blur-sm flex-shrink-0">
      <span className="text-xs font-medium text-white/60 truncate max-w-[200px]">{doc.name}</span>
      <div className="flex items-center gap-0.5">
        {blobUrl && (
          <>
            <button
              onClick={handleDownload}
              className="p-1.5 rounded text-white/40 hover:text-white/80 hover:bg-white/5 transition-colors"
              title="Download"
            >
              <Download className="h-3.5 w-3.5" />
            </button>
            <a
              href={blobUrl}
              target="_blank"
              rel="noreferrer"
              className="p-1.5 rounded text-white/40 hover:text-white/80 hover:bg-white/5 transition-colors"
              title="Open in new tab"
            >
              <Maximize2 className="h-3.5 w-3.5" />
            </a>
          </>
        )}
      </div>
    </div>
  );

  if (isSeedDoc) {
    return (
      <div className="flex flex-col h-full bg-[#1a1a1a]">
        {toolbar}
        <div className="flex-1 flex flex-col items-center justify-center gap-3 text-white/30">
          <FileText className="h-12 w-12" />
          <p className="text-xs tracking-wide">Sample document — no file stored</p>
        </div>
      </div>
    );
  }

  if (!blobUrl && !fetchError) {
    return (
      <div className="flex flex-col h-full bg-[#1a1a1a]">
        {toolbar}
        <div className="flex-1 flex items-center justify-center">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-white/20 border-t-white/60" />
        </div>
      </div>
    );
  }

  if (fetchError) {
    return (
      <div className="flex flex-col h-full bg-[#1a1a1a]">
        {toolbar}
        <div className="flex-1 flex flex-col items-center justify-center gap-3 text-white/40">
          <FileText className="h-10 w-10 text-white/20" />
          <p className="text-xs">Could not load document</p>
        </div>
      </div>
    );
  }

  if (PDF_TYPES.has(ft)) {
    return (
      <div className="flex flex-col h-full bg-[#1a1a1a]">
        {toolbar}
        <iframe
          src={blobUrl!}
          className="flex-1 w-full border-0"
          title={doc.name}
        />
      </div>
    );
  }

  if (IMAGE_TYPES.has(ft)) {
    return (
      <div className="flex flex-col h-full bg-[#1a1a1a]">
        {toolbar}
        <div className="flex-1 flex items-center justify-center p-6 overflow-auto min-h-0">
          <img
            src={blobUrl!}
            alt={doc.name}
            className="max-w-full max-h-full object-contain shadow-2xl rounded-sm"
          />
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-[#1a1a1a]">
      {toolbar}
      <div className="flex-1 flex flex-col items-center justify-center gap-4 text-white/50">
        <FileText className="h-12 w-12 text-white/20" />
        <p className="text-sm font-medium">{doc.name}</p>
        <button
          onClick={handleDownload}
          className="px-4 py-2 rounded-lg bg-white/10 hover:bg-white/15 text-white/70 hover:text-white text-sm transition-colors flex items-center gap-2"
        >
          <Download className="h-4 w-4" /> Download file
        </button>
      </div>
    </div>
  );
}

// ─── Detail view ──────────────────────────────────────────────────────────────

function DocDetailView({
  doc,
  onClose,
  onSave,
}: {
  doc: ProcessedDoc;
  onClose: () => void;
  onSave: (d: ProcessedDoc) => void;
}) {
  const [fields, setFields]       = useState<ExtractedField[]>(doc.headerFields);
  const [lineItems, setLineItems] = useState<LineItem[]>(doc.lineItems);
  const [notes, setNotes]         = useState("");
  const [saving, setSaving]       = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showTrace, setShowTrace] = useState(false);
  const [showMatchPanel, setShowMatchPanel] = useState(false);
  const [showRunMatchModal, setShowRunMatchModal] = useState<"2way" | "3way" | null>(null);
  const isReadOnly = doc.status === "auto_approved" || doc.status === "reviewed";

  const { data: matchResult, isLoading: isLoadingMatch, refetch: refetchMatch } = useGetMatchResults(
    { documentId: doc.id },
    { enabled: showMatchPanel },
  );
  const triggerMatch = useTriggerMatch(doc.id);

  // Persistence: PATCH edited field values, then POST the review (records reviewer + timestamp,
  // moves the doc to the Reviewed tab). The hooks invalidate the queries on success so the list
  // and detail refetch automatically.
  const patchFields = usePatchProcessedDocFields(doc.id);
  const postReview  = usePostProcessedDocReview(doc.id);

  // Sync local state when the detail data arrives after initial render.
  // useState only uses the prop value on mount; subsequent prop updates are ignored
  // unless we explicitly sync here. We only overwrite when the incoming data is
  // richer (non-empty) so in-progress user edits are never discarded.
  const userHasEdited = useRef(false);
  useEffect(() => {
    if (!userHasEdited.current && doc.headerFields.length > 0) {
      setFields(doc.headerFields);
    }
  }, [doc.headerFields.length]);
  useEffect(() => {
    if (!userHasEdited.current && doc.lineItems.length > 0) {
      setLineItems(doc.lineItems);
    }
  }, [doc.lineItems.length]);

  const updateField = (key: string, value: string) => {
    userHasEdited.current = true;
    setFields((prev) => prev.map((f) => f.key === key ? { ...f, value, changed: f.value !== value } : f));
  };

  const updateCell = (id: string, col: string, value: string) => {
    userHasEdited.current = true;
    setLineItems((prev) => prev.map((r) => r.id === id ? { ...r, [col]: value } : r));
  };


  // draft=true → "Save as Draft": persist edits only, keep the doc in Pending Review.
  // draft=false → "Submit": persist edits + finalize (records reviewer, moves to Reviewed).
  const handleSave = async (draft: boolean) => {
    setSaving(true);
    setSaveError(null);
    let editsSaved = false;
    try {
      // Build the field-update payload from the DB ids carried through enrichWithDetail.
      // (Add Row / row-delete are disabled for backend docs — there are no create/delete
      // line-item endpoints — so only existing cells are edited, and every cell id resolves.)
      const headers: FieldUpdateItem[] = fields
        .filter((f) => f.fieldId)
        .map((f) => ({ id: f.fieldId as string, value: f.value === "" ? null : f.value }));
      const line_items: FieldUpdateItem[] = [];
      lineItems.forEach((row) => {
        const ids = doc.cellIds?.[row.id];
        if (!ids) return;
        doc.lineItemColumns.forEach((col) => {
          const cellId = ids[col];
          if (cellId) line_items.push({ id: cellId, value: (row[col] ?? "") === "" ? null : row[col] });
        });
      });

      // Always persist the edited values. For a draft, pass draft:true so the backend
      // tags the doc (review_draft) and leaves it in Pending Review.
      if (headers.length || line_items.length || draft) {
        await patchFields.mutateAsync({ headers, line_items, draft });
        editsSaved = true;
      }

      if (draft) {
        // Reflect the draft locally + close back to Pending (the hooks already refetched the list).
        onSave({ ...doc, headerFields: fields, lineItems, status: "pending", reviewDraft: true });
        return;
      }

      await postReview.mutateAsync({ notes: notes || null });
      // Only on success: reflect the review locally + close (the hooks already refetched).
      onSave({
        ...doc,
        headerFields: fields,
        lineItems,
        status: "reviewed",
        reviewDraft: false,
        reviewer: "me",
        reviewedAt: new Date().toISOString().slice(0, 16).replace("T", " "),
      });
    } catch (e) {
      console.error("[ProcessedDocs] save review failed", e);
      setSaveError(
        draft
          ? "Could not save the draft — nothing was persisted. Please try again."
          : editsSaved
            ? "Your field edits were saved, but submitting the review failed. Please reopen the document and try again."
            : "Could not submit the review — nothing was persisted. Please try again.",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex h-full overflow-hidden">

      {/* ════════════ LEFT PANEL ════════════ */}
      <div className="flex flex-col overflow-hidden border-r" style={{ flex: "0 0 58%" }}>

        {/* ── doc header ── */}
        <div className="flex-shrink-0 px-6 py-4 border-b bg-muted/5">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0 space-y-1.5">
              <h2 className="text-base font-semibold leading-tight truncate">{doc.name}</h2>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <span>Agent: <span className="text-foreground font-medium">{doc.sourceAgent}</span></span>
                <span className="text-border">·</span>
                <span>{doc.dateProcessed}</span>
                <span className="text-border">·</span>
                <Badge variant="outline" className="text-[11px] px-1.5 py-0">{doc.documentType}</Badge>
                <ConfidencePill score={doc.overallConfidence} />
                <StatusChip status={doc.status} />
                {doc.reviewDraft && doc.status === "pending" && (
                  <Badge variant="outline" className="text-[11px] px-1.5 py-0 border-amber-400 text-amber-600">
                    Draft
                  </Badge>
                )}
              </div>
              {doc.status === "reviewed" && (
                <p className="text-[11px] text-muted-foreground">
                  Reviewed by <span className="font-medium text-foreground">{doc.reviewer}</span> · {doc.reviewedAt}
                </p>
              )}
            </div>
            <button
              onClick={onClose}
              className="mt-0.5 p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors flex-shrink-0"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* ── scrollable body ── */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-7 min-h-0">

          {/* Header fields */}
          <section>
            <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/60 mb-3">
              Extracted Header Fields
            </p>
            <div className="rounded-xl border divide-y overflow-hidden">
              {fields.map((f) => (
                <div
                  key={f.key}
                  className={cn(
                    "grid items-center gap-3 px-4 py-2.5 transition-colors",
                    "grid-cols-[160px_1fr_auto]",
                    f.changed && "bg-amber-400/5 border-l-2 border-l-amber-400",
                  )}
                >
                  <span className="text-[11px] font-mono text-muted-foreground truncate">{f.key}</span>
                  {isReadOnly ? (
                    <span className="text-sm font-medium">
                      {f.value || <span className="italic text-muted-foreground/50 font-normal">—</span>}
                    </span>
                  ) : (
                    <Input
                      value={f.value}
                      onChange={(e) => updateField(f.key, e.target.value)}
                      className="h-7 text-sm bg-transparent border-transparent hover:border-input focus:border-[#D04A02] transition-colors px-2"
                    />
                  )}
                  <div className="flex items-center gap-1.5 justify-end">
                    {(f.reasoning || f.source) && (
                      <span title={evidenceTitle(f)} className="text-muted-foreground/40 hover:text-foreground cursor-help" aria-label={`Extraction evidence — ${evidenceTitle(f)}`}>
                        <Info className="h-3.5 w-3.5" />
                      </span>
                    )}
                    <ConfidencePill score={f.confidence} />
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* Line items */}
          {doc.lineItemColumns.length > 0 && (
            <section>
              <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/60 mb-3">
                Line Items
              </p>
              <div className="rounded-xl border overflow-hidden">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-muted/30 hover:bg-muted/30">
                      {doc.lineItemColumns.map((col) => (
                        <TableHead key={col} className="text-[11px] font-semibold uppercase tracking-wide py-2">{col}</TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {lineItems.map((row) => (
                      <TableRow key={row.id} className="hover:bg-muted/20">
                        {doc.lineItemColumns.map((col) => (
                          <TableCell key={col} className="py-2">
                            {isReadOnly ? (
                              <span className="text-sm">{row[col]}</span>
                            ) : (
                              <Input
                                value={row[col] ?? ""}
                                onChange={(e) => updateCell(row.id, col, e.target.value)}
                                className="h-7 text-sm bg-transparent border-transparent hover:border-input focus:border-[#D04A02] px-2"
                              />
                            )}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              {/* Add Row / row-delete removed: editing existing cell values persists via PATCH,
                  but there is no backend endpoint to create or delete line-item rows yet. */}
            </section>
          )}

          {/* Processing trace — the complete per-component cycle (input → output) for this document. */}
          <section>
            <div className="flex items-center justify-between gap-2">
              <button
                onClick={() => setShowTrace((v) => !v)}
                className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest text-muted-foreground/60 hover:text-foreground transition-colors"
              >
                <ListTree className="h-3.5 w-3.5" />
                {showTrace ? "Hide" : "View"} Processing Logs
              </button>
              <button
                onClick={() => downloadDocLog(doc)}
                title="Download processing log"
                className="flex items-center gap-1 text-[10px] font-medium text-muted-foreground/60 hover:text-foreground transition-colors"
              >
                <Download className="h-3 w-3" /> Download log
              </button>
            </div>
            {showTrace && (
              <div className="mt-2 rounded-xl border bg-muted/5 overflow-hidden">
                <ProcessingTrace docId={doc.id} />
              </div>
            )}
          </section>

          {/* SAP Match Results */}
          <section>
            <div className="flex items-center justify-between gap-2">
              <button
                onClick={() => setShowMatchPanel((v) => !v)}
                className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest text-muted-foreground/60 hover:text-foreground transition-colors"
              >
                <GitCompare className="h-3.5 w-3.5" />
                {showMatchPanel ? "Hide" : "View"} SAP Match Results
              </button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button size="sm" variant="outline" className="h-7 text-xs gap-1.5 rounded-lg">
                    <GitCompare className="h-3 w-3" /> Run Match
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => setShowRunMatchModal("2way")}>
                    2-Way Match (Invoice vs PO)
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setShowRunMatchModal("3way")}>
                    3-Way Match (Invoice vs PO vs GR)
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
            {showMatchPanel && (
              <div className="mt-2 rounded-xl border bg-muted/5 overflow-hidden p-3">
                <MatchResultsPanel
                  documentId={doc.id}
                  matchResult={matchResult}
                  isLoading={isLoadingMatch}
                  onTriggerMatch={() => setShowRunMatchModal("2way")}
                />
              </div>
            )}
          </section>

          {showRunMatchModal && (
            <RunMatchModal
              documentId={doc.id}
              defaultMatchType={showRunMatchModal}
              onClose={() => setShowRunMatchModal(null)}
              onSuccess={() => {
                setShowRunMatchModal(null);
                setShowMatchPanel(true);
                setTimeout(() => refetchMatch(), 2500);
              }}
            />
          )}

          {/* Review actions */}
          {!isReadOnly && (
            <section className="space-y-3 pb-4">
              <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/60">
                Review
              </p>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={2}
                placeholder="Add review notes (optional)…"
                className="w-full rounded-xl border bg-background/50 text-sm px-4 py-3 resize-none focus:outline-none focus:ring-2 focus:ring-[#D04A02]/30 focus:border-[#D04A02] transition-all placeholder:text-muted-foreground/40"
              />
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  onClick={() => handleSave(true)}
                  disabled={saving}
                  className="gap-2 rounded-lg font-medium disabled:opacity-60"
                >
                  <Save className="h-4 w-4" /> {saving ? "Saving…" : "Save as Draft"}
                </Button>
                <Button
                  onClick={() => handleSave(false)}
                  disabled={saving}
                  className="bg-[#D04A02] hover:bg-[#B84000] text-white gap-2 rounded-lg font-medium disabled:opacity-60"
                >
                  <CheckCircle2 className="h-4 w-4" /> {saving ? "Saving…" : "Submit"}
                </Button>
              </div>
              {saveError && (
                <p className="text-xs text-destructive mt-1">{saveError}</p>
              )}
            </section>
          )}

          {doc.status === "auto_approved" && (
            <section className="pb-4">
              <Button
                variant="outline"
                size="sm"
                onClick={() => onSave({ ...doc, status: "pending" })}
                className="gap-1.5 text-xs rounded-lg"
              >
                <RotateCcw className="h-3.5 w-3.5" /> Override & Review
              </Button>
            </section>
          )}
        </div>
      </div>

      {/* ════════════ RIGHT PANEL — document viewer ════════════ */}
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
        <DocumentViewer doc={doc} />
      </div>

    </div>
  );
}

// ─── Document list table ──────────────────────────────────────────────────────

function DocTable({
  docs,
  onView,
}: {
  docs: ProcessedDoc[];
  onView: (doc: ProcessedDoc) => void;
}) {
  if (docs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-muted-foreground gap-3">
        <div className="h-14 w-14 rounded-2xl bg-muted/30 flex items-center justify-center">
          <FileText className="h-7 w-7 opacity-30" />
        </div>
        <p className="text-sm font-medium">No documents here yet.</p>
      </div>
    );
  }
  return (
    <div className="rounded-xl border overflow-hidden">
      <Table>
        <TableHeader>
          <TableRow className="bg-muted/30 hover:bg-muted/30">
            <TableHead className="font-semibold text-xs uppercase tracking-wide">Document</TableHead>
            <TableHead className="font-semibold text-xs uppercase tracking-wide w-40">Agent</TableHead>
            <TableHead className="font-semibold text-xs uppercase tracking-wide w-40">Processed</TableHead>
            <TableHead className="font-semibold text-xs uppercase tracking-wide w-32">Type</TableHead>
            <TableHead className="font-semibold text-xs uppercase tracking-wide w-28 text-center">Confidence</TableHead>
            <TableHead className="font-semibold text-xs uppercase tracking-wide w-36">Status</TableHead>
            <TableHead className="w-24 text-right" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {docs.map((doc) => (
            <TableRow
              key={doc.id}
              className="cursor-pointer group hover:bg-muted/20 transition-colors"
              onClick={() => onView(doc)}
            >
              <TableCell>
                <div className="flex items-center gap-2.5">
                  <div className="h-7 w-7 rounded-lg bg-[#D04A02]/8 flex items-center justify-center flex-shrink-0">
                    <FileText className="h-3.5 w-3.5 text-[#D04A02]/60" />
                  </div>
                  <span className="font-medium text-sm">{doc.name}</span>
                </div>
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">{doc.sourceAgent}</TableCell>
              <TableCell className="text-sm text-muted-foreground tabular-nums">{doc.dateProcessed}</TableCell>
              <TableCell>
                <Badge variant="outline" className="text-xs rounded-md">{doc.documentType}</Badge>
              </TableCell>
              <TableCell className="text-center">
                <ConfidencePill score={doc.overallConfidence} />
              </TableCell>
              <TableCell>
                <div className="flex items-center gap-1.5">
                  <StatusChip status={doc.status} />
                  {doc.reviewDraft && doc.status === "pending" && (
                    <Badge variant="outline" className="text-[10px] px-1.5 py-0 border-amber-400 text-amber-600">
                      Draft
                    </Badge>
                  )}
                </div>
              </TableCell>
              <TableCell>
                <div className="flex items-center justify-end gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button
                    title="View"
                    onClick={(e) => { e.stopPropagation(); onView(doc); }}
                    className="p-1.5 rounded-lg hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <Eye className="h-3.5 w-3.5" />
                  </button>
                  <button
                    title="Export CSV"
                    onClick={(e) => { e.stopPropagation(); exportDocsToCsv([doc], `${doc.name || "document"}.csv`); }}
                    className="p-1.5 rounded-lg hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <Download className="h-3.5 w-3.5" />
                  </button>
                  {/* Delete hidden: no backend DELETE endpoint for processed docs yet. */}
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function ProcessedDocsPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [search, setSearch]         = useState("");
  const [activeTab, setActiveTab]   = useState<DocStatus>("pending");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filterDocType, setFilterDocType] = useState("all");

  // Open a specific document when navigated here with ?doc=<id>
  useEffect(() => {
    const docId = searchParams.get("doc");
    if (docId) setSelectedId(docId);
  }, []);

  // Live data from the IDP backend only. (Demo SEED_DOCS removed so review queues / counts /
  // tabs / search reflect real documents, not samples. SEED_DOCS definition kept but unused.)
  const { data: docsPage } = useGetProcessedDocs({ size: 100 });
  const docs: ProcessedDoc[] = useMemo(
    () => [...(docsPage?.items ?? []).map(listDocToPage) /*, ...SEED_DOCS */],
    [docsPage],
  );
  // Extracted fields/line-items come from the detail endpoint, fetched on select.
  const { data: detail, isLoading: isLoadingDetail } = useGetProcessedDoc({ id: selectedId ?? "" });
  const selectedDoc = useMemo(
    () => (selectedId ? enrichWithDetail(docs.find((d) => d.id === selectedId), detail) : null),
    [selectedId, docs, detail],
  );

  const docTypes = useMemo(() => {
    const types = Array.from(new Set(docs.map((d) => d.documentType)));
    return ["all", ...types];
  }, [docs]);

  const getFiltered = (status: DocStatus) =>
    docs.filter((d) => {
      const matchStatus = d.status === status;
      const matchSearch =
        !search ||
        d.name.toLowerCase().includes(search.toLowerCase()) ||
        d.sourceAgent.toLowerCase().includes(search.toLowerCase()) ||
        d.documentType.toLowerCase().includes(search.toLowerCase());
      const matchType = filterDocType === "all" || d.documentType === filterDocType;
      return matchStatus && matchSearch && matchType;
    });

  const counts = useMemo(() => ({
    pending:       docs.filter((d) => d.status === "pending").length,
    auto_approved: docs.filter((d) => d.status === "auto_approved").length,
    reviewed:      docs.filter((d) => d.status === "reviewed").length,
    skipped:       docs.filter((d) => d.status === "skipped").length,
  }), [docs]);

  // NOTE: persisting edits (PATCH /fields) + review/approve mutations are wired
  // in a follow-up; for now the detail view edits stay local until navigation.
  const handleSave = (_updated: ProcessedDoc) => {
    setSelectedId(null);
  };

  // ── Split-view (detail) ──
  if (selectedId) {
    const backBar = (
      <div className="flex-shrink-0 flex items-center gap-3 px-4 py-2 border-b bg-muted/5">
        <button
          onClick={() => { setSelectedId(null); navigate("/processed-docs", { replace: true }); }}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ChevronLeft className="h-4 w-4" />
          Back to list
        </button>
      </div>
    );

    // Show a skeleton while the detail API call is in-flight so the user never
    // sees the view open with empty fields and then populate a moment later.
    if (isLoadingDetail || !selectedDoc) {
      return (
        <div className="flex flex-col h-full bg-background overflow-hidden">
          {backBar}
          <div className="flex-1 flex items-center justify-center">
            <div className="flex flex-col items-center gap-3 text-muted-foreground">
              <div className="h-6 w-6 animate-spin rounded-full border-2 border-[#D04A02] border-t-transparent" />
              <p className="text-sm">Loading document…</p>
            </div>
          </div>
        </div>
      );
    }

    return (
      <div className="flex flex-col h-full bg-background overflow-hidden">
        {/* back bar */}
        {backBar}
        <div className="flex-1 overflow-hidden">
          <DocDetailView
            doc={selectedDoc}
            onClose={() => setSelectedId(null)}
            onSave={handleSave}
          />
        </div>
      </div>
    );
  }

  // ── List view ──
  return (
    <div className="flex flex-col h-full bg-background overflow-hidden">

      {/* ── Page header ── */}
      <div className="flex-shrink-0 border-b px-6 py-4 flex items-center justify-between bg-muted/5">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Processed Documents</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Review and manage documents extracted by your IDP agents.
          </p>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="gap-1.5 rounded-lg">
              <Download className="h-4 w-4" /> Export
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => exportDocsToCsv(docs, "processed-docs.csv")}>
              Export as CSV
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* ── Tabs ── */}
      <Tabs
        value={activeTab}
        onValueChange={(v) => setActiveTab(v as DocStatus)}
        className="flex-1 flex flex-col overflow-hidden"
      >
        <div className="flex-shrink-0 px-6 pt-3 border-b">
          <TabsList className="h-9 bg-transparent p-0 gap-1">
            {([
              { value: "pending",       label: "Pending Review",  icon: AlertCircle,    count: counts.pending       },
              { value: "auto_approved", label: "Auto-Approved",   icon: CheckCircle2,   count: counts.auto_approved },
              { value: "reviewed",      label: "Reviewed",        icon: CheckCircle2,   count: counts.reviewed      },
              { value: "skipped",       label: "Skipped",         icon: MinusCircle,    count: counts.skipped       },
            ] as const).map(({ value, label, icon: Icon, count }) => (
              <TabsTrigger
                key={value}
                value={value}
                className={cn(
                  "h-9 rounded-none border-b-2 border-transparent px-4 text-sm font-medium text-muted-foreground transition-all",
                  "data-[state=active]:border-[#D04A02] data-[state=active]:text-foreground data-[state=active]:shadow-none",
                  "data-[state=active]:bg-transparent hover:text-foreground",
                )}
              >
                <Icon className="h-3.5 w-3.5 mr-1.5" />
                {label}
                <span className={cn(
                  "ml-1.5 rounded-full px-1.5 py-0 text-[10px] font-semibold tabular-nums",
                  activeTab === value
                    ? "bg-[#D04A02]/10 text-[#D04A02]"
                    : "bg-muted text-muted-foreground",
                )}>
                  {count}
                </span>
              </TabsTrigger>
            ))}
          </TabsList>
        </div>

        {(["pending", "auto_approved", "reviewed", "skipped"] as const).map((tab) => (
          <TabsContent key={tab} value={tab} className="flex-1 overflow-auto m-0">
            <div className="px-6 py-5 space-y-4">

              {/* Toolbar */}
              <div className="flex items-center gap-3 flex-wrap">
                <div className="relative flex-1 max-w-sm">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Search documents, agents…"
                    className="pl-9 rounded-lg"
                  />
                </div>
                <Select value={filterDocType} onValueChange={setFilterDocType}>
                  <SelectTrigger className="w-40 h-9 rounded-lg">
                    <SelectValue placeholder="Document type" />
                  </SelectTrigger>
                  <SelectContent>
                    {docTypes.map((t) => (
                      <SelectItem key={t} value={t}>{t === "all" ? "All types" : t}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {(search || filterDocType !== "all") && (
                  <button
                    onClick={() => { setSearch(""); setFilterDocType("all"); }}
                    className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <X className="h-3.5 w-3.5" /> Clear
                  </button>
                )}
              </div>

              <DocTable docs={getFiltered(tab)} onView={(d) => setSelectedId(d.id)} />
            </div>
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}
