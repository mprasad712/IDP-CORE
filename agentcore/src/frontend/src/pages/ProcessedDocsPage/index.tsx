import {
  Search,
  Download,
  Trash2,
  Eye,
  ChevronLeft,
  ZoomIn,
  ZoomOut,
  Maximize2,
  FileText,
  CheckCircle2,
  Clock,
  AlertCircle,
  X,
  Plus,
  RotateCcw,
  ChevronUp,
  ChevronDown,
} from "lucide-react";
import { useState, useMemo } from "react";
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

// ─── Types ────────────────────────────────────────────────────────────────────

type DocStatus = "pending" | "auto_approved" | "reviewed";

interface ExtractedField {
  key: string;
  value: string;
  confidence: number;
  changed?: boolean;
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
  overallConfidence: number;
  status: DocStatus;
  headerFields: ExtractedField[];
  lineItems: LineItem[];
  lineItemColumns: string[];
  reviewer?: string;
  reviewedAt?: string;
}

// ─── Seed data ────────────────────────────────────────────────────────────────

const SEED_DOCS: ProcessedDoc[] = [
  {
    id: "1",
    name: "INV-2026-00142.pdf",
    sourceAgent: "Invoice Processor",
    dateProcessed: "2026-06-04 09:12",
    documentType: "Invoice",
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
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-400/10 border border-amber-400/20 px-2.5 py-0.5 text-xs font-medium text-amber-600 dark:text-amber-400">
      <Clock className="h-3 w-3" /> Pending Review
    </span>
  );
}

// ─── Document Viewer ──────────────────────────────────────────────────────────

function DocumentViewer({ doc }: { doc: ProcessedDoc }) {
  const [zoom, setZoom] = useState(100);

  return (
    <div className="flex flex-col h-full bg-[#1a1a1a]">
      {/* toolbar */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-white/5 bg-[#111]/60 backdrop-blur-sm flex-shrink-0">
        <span className="text-xs font-medium text-white/60 truncate max-w-[200px]">{doc.name}</span>
        <div className="flex items-center gap-0.5">
          <button
            onClick={() => setZoom((z) => Math.max(40, z - 10))}
            className="p-1.5 rounded text-white/40 hover:text-white/80 hover:bg-white/5 transition-colors"
          >
            <ZoomOut className="h-3.5 w-3.5" />
          </button>
          <span className="text-xs text-white/50 w-10 text-center tabular-nums">{zoom}%</span>
          <button
            onClick={() => setZoom((z) => Math.min(200, z + 10))}
            className="p-1.5 rounded text-white/40 hover:text-white/80 hover:bg-white/5 transition-colors"
          >
            <ZoomIn className="h-3.5 w-3.5" />
          </button>
          <div className="w-px h-4 bg-white/10 mx-1" />
          <button className="p-1.5 rounded text-white/40 hover:text-white/80 hover:bg-white/5 transition-colors">
            <Maximize2 className="h-3.5 w-3.5" />
          </button>
          <button className="p-1.5 rounded text-white/40 hover:text-white/80 hover:bg-white/5 transition-colors">
            <Download className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* viewer — fills all remaining height */}
      <div className="flex-1 flex items-center justify-center p-6 overflow-auto min-h-0">
        <div
          className="relative bg-white shadow-2xl rounded-sm flex items-center justify-center transition-all duration-150 ease-out"
          style={{
            width: `${zoom}%`,
            maxWidth: "640px",
            aspectRatio: "1 / 1.414",
          }}
        >
          {/* watermark / placeholder */}
          <div className="absolute inset-0 flex flex-col items-center justify-center select-none">
            <FileText className="h-12 w-12 text-gray-200" />
            <p className="mt-2 text-[10px] text-gray-300 tracking-wide">Document preview</p>
          </div>
          {/* subtle page lines */}
          <div className="absolute inset-x-8 top-10 space-y-3 pointer-events-none">
            {Array.from({ length: 14 }).map((_, i) => (
              <div key={i} className="h-px bg-gray-100" />
            ))}
          </div>
        </div>
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
  const isReadOnly = doc.status === "auto_approved" || doc.status === "reviewed";

  const updateField = (key: string, value: string) =>
    setFields((prev) => prev.map((f) => f.key === key ? { ...f, value, changed: f.value !== value } : f));

  const updateCell = (id: string, col: string, value: string) =>
    setLineItems((prev) => prev.map((r) => r.id === id ? { ...r, [col]: value } : r));

  const addRow = () => {
    const empty: LineItem = { id: `r${Date.now()}` };
    doc.lineItemColumns.forEach((col) => { empty[col] = ""; });
    setLineItems((prev) => [...prev, empty]);
  };

  const handleSave = () => {
    onSave({
      ...doc,
      headerFields: fields,
      lineItems,
      status: "reviewed",
      reviewer: "me",
      reviewedAt: new Date().toISOString().slice(0, 16).replace("T", " "),
    });
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
                  <ConfidencePill score={f.confidence} />
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
                      {!isReadOnly && <TableHead className="w-8" />}
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
                        {!isReadOnly && (
                          <TableCell className="py-2">
                            <button
                              onClick={() => setLineItems((p) => p.filter((r) => r.id !== row.id))}
                              className="text-muted-foreground/40 hover:text-destructive transition-colors"
                            >
                              <X className="h-3.5 w-3.5" />
                            </button>
                          </TableCell>
                        )}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              {!isReadOnly && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={addRow}
                  className="mt-2 gap-1.5 text-xs text-muted-foreground hover:text-foreground"
                >
                  <Plus className="h-3.5 w-3.5" /> Add Row
                </Button>
              )}
            </section>
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
              <Button
                onClick={handleSave}
                className="bg-[#D04A02] hover:bg-[#B84000] text-white gap-2 rounded-lg font-medium"
              >
                <CheckCircle2 className="h-4 w-4" /> Save Review
              </Button>
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
                <StatusChip status={doc.status} />
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
                    title="Export"
                    onClick={(e) => e.stopPropagation()}
                    className="p-1.5 rounded-lg hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <Download className="h-3.5 w-3.5" />
                  </button>
                  <button
                    title="Delete"
                    onClick={(e) => e.stopPropagation()}
                    className="p-1.5 rounded-lg hover:bg-accent text-muted-foreground hover:text-destructive transition-colors"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
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
  const [docs, setDocs]             = useState<ProcessedDoc[]>(SEED_DOCS);
  const [search, setSearch]         = useState("");
  const [activeTab, setActiveTab]   = useState<DocStatus>("pending");
  const [selectedDoc, setSelectedDoc] = useState<ProcessedDoc | null>(null);
  const [filterDocType, setFilterDocType] = useState("all");

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
  }), [docs]);

  const handleSave = (updated: ProcessedDoc) => {
    setDocs((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
    setSelectedDoc(updated);
  };

  // ── Split-view (detail) ──
  if (selectedDoc) {
    return (
      <div className="flex flex-col h-full bg-background overflow-hidden">
        {/* back bar */}
        <div className="flex-shrink-0 flex items-center gap-3 px-4 py-2 border-b bg-muted/5">
          <button
            onClick={() => setSelectedDoc(null)}
            className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <ChevronLeft className="h-4 w-4" />
            Back to list
          </button>
        </div>
        <div className="flex-1 overflow-hidden">
          <DocDetailView
            doc={selectedDoc}
            onClose={() => setSelectedDoc(null)}
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
            <DropdownMenuItem>Export as CSV</DropdownMenuItem>
            <DropdownMenuItem>Export as Excel</DropdownMenuItem>
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

        {(["pending", "auto_approved", "reviewed"] as const).map((tab) => (
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

              <DocTable docs={getFiltered(tab)} onView={setSelectedDoc} />
            </div>
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}
