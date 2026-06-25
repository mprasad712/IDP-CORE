import {
  BarChart3,
  ChevronLeft,
  ChevronRight,
  Download,
  FileText,
  Loader2,
  ScrollText,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api } from "@/controllers/API/api";
import { getURL } from "@/controllers/API/helpers/constants";
import { useGetFieldConfigs } from "@/controllers/API/queries/field-configs";
import { useGetReport, type ReportRow } from "@/controllers/API/queries/idp";

// Document statuses worth filtering a report by (subset of the idp_documents status set).
const STATUS_OPTIONS = [
  "all",
  "pending_review",
  "auto_approved",
  "reviewed",
  "failed",
  "skipped",
  "processing",
  "queued",
  "extracted",
];

// format key -> file extension (matches the backend serializers)
const EXT: Record<string, string> = {
  csv: "csv",
  excel: "xlsx",
  xml: "xml",
  json: "json",
  txt: "txt",
};
const DOC_FORMATS = ["csv", "excel", "xml", "json", "txt"]; // per-PO export
const TABLE_FORMATS = ["csv", "excel", "xml"]; // report / all-data export
const FORMAT_LABEL: Record<string, string> = {
  csv: "CSV",
  excel: "Excel",
  xml: "XML",
  json: "JSON",
  txt: "Text",
};

const PAGE_SIZE = 25;

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString();
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

function statusClass(status: string): string {
  switch (status) {
    case "reviewed":
    case "auto_approved":
      return "bg-emerald-50 text-emerald-700 border-emerald-200";
    case "pending_review":
      return "bg-amber-50 text-amber-700 border-amber-200";
    case "failed":
    case "skipped":
      return "bg-red-50 text-red-700 border-red-200";
    default:
      return "bg-muted text-muted-foreground border-border";
  }
}

async function downloadBlob(
  url: string,
  params: Record<string, string>,
  filename: string,
): Promise<void> {
  const res = await api.get(url, { params, responseType: "blob" });
  const blobUrl = URL.createObjectURL(res.data as Blob);
  const a = document.createElement("a");
  a.href = blobUrl;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(blobUrl);
}

export default function ReportsPage() {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("");
  const [values, setValues] = useState<"both" | "final" | "predicted" | "audited">("both");
  const [configId, setConfigId] = useState("all"); // "all" = sectioned layout; else a field-config id
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);

  // Convert the date-range inputs into inclusive datetime bounds for the backend.
  const createdStart = start ? `${start}T00:00:00` : undefined;
  const createdEnd = end ? `${end}T23:59:59` : undefined;
  const statusParam = statusFilter !== "all" ? statusFilter : undefined;
  const typeParam = typeFilter.trim() || undefined;

  const { data, isLoading, isError } = useGetReport({
    page,
    size: PAGE_SIZE,
    status_filter: statusParam,
    predicted_type: typeParam,
    created_start: createdStart,
    created_end: createdEnd,
    // A selected Field config filters the table to docs extracted with it (same as the export).
    config_id: configId !== "all" ? configId : undefined,
  });

  const rows: ReportRow[] = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = data?.pages ?? 1;

  // Field configurations (templates + custom) for the all-data export "type" dropdown.
  // size=100 is the pagination max; orgs with >100 configs are a known limitation (use a
  // searchable picker later — the free-text Type filter is an alternative in the meantime).
  const { data: cfgPage } = useGetFieldConfigs({ size: 100, is_active: true });
  const configs = useMemo(
    () => [...(cfgPage?.items ?? [])].sort((a, b) => a.name.localeCompare(b.name)),
    [cfgPage],
  );

  // Shared query params for the file-download endpoints (everything except pagination).
  const filterQP = useMemo(() => {
    const qp: Record<string, string> = {};
    if (statusParam) qp.status_filter = statusParam;
    if (typeParam) qp.predicted_type = typeParam;
    if (createdStart) qp.created_start = createdStart;
    if (createdEnd) qp.created_end = createdEnd;
    return qp;
  }, [statusParam, typeParam, createdStart, createdEnd]);

  function resetToFirstPage(setter: (v: string) => void) {
    return (v: string) => {
      setter(v);
      setPage(1);
    };
  }

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error("[Reports] download failed", e);
    } finally {
      setBusy(false);
    }
  }

  function downloadReport(fmt: string) {
    const params: Record<string, string> = { ...filterQP, format: fmt };
    if (configId !== "all") params.config_id = configId; // match the config-filtered table
    run(() =>
      downloadBlob(
        `${getURL("IDP_REPORTS")}/processed-docs/export`,
        params,
        `processed_docs_report.${EXT[fmt]}`,
      ),
    );
  }

  function downloadAllData(fmt: string) {
    // A selected config switches the backend to the config-driven per-document-row layout;
    // "all" keeps the sectioned layout.
    const params: Record<string, string> = { ...filterQP, format: fmt, values };
    if (configId !== "all") params.config_id = configId;
    run(() =>
      downloadBlob(
        `${getURL("IDP_REPORTS")}/processed-docs/export-data`,
        params,
        `processed_docs_data.${EXT[fmt]}`,
      ),
    );
  }

  function exportDoc(row: ReportRow, fmt: string) {
    const base = row.original_filename.replace(/\.[^.]+$/, "") || "document";
    // The per-document export endpoint only supports {both, final}; clamp the richer modes.
    const docValues = values === "both" || values === "final" ? values : "final";
    run(() =>
      downloadBlob(
        `${getURL("IDP_PROCESSED_DOCS")}/${row.document_id}/export`,
        { format: fmt, values: docValues },
        `${base}_export.${EXT[fmt]}`,
      ),
    );
  }

  function downloadLog(row: ReportRow) {
    const base = row.original_filename.replace(/\.[^.]+$/, "") || "document";
    run(() =>
      downloadBlob(
        `${getURL("IDP_DOCUMENTS")}/${row.document_id}/log/download`,
        {},
        `${base}_processing_log.txt`,
      ),
    );
  }

  return (
    <div className="flex flex-col h-full bg-background overflow-hidden">
      {/* Header */}
      <div className="flex-shrink-0 border-b px-6 py-4 flex items-center justify-between bg-muted/5">
        <div>
          <h1 className="text-xl font-semibold tracking-tight flex items-center gap-2">
            <BarChart3 className="h-5 w-5 text-[#D04A02]" />
            Reports
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Processed-document report for a date range — timeline, status, and downloadable exports.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* Values toggle (applies to per-PO and all-data exports) */}
          <Select value={values} onValueChange={(v) => setValues(v as typeof values)}>
            <SelectTrigger className="w-44 h-9 rounded-lg">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="both">Predicted + Audited</SelectItem>
              <SelectItem value="predicted">Predicted only</SelectItem>
              <SelectItem value="audited">Audited only</SelectItem>
              <SelectItem value="final">Final values only</SelectItem>
            </SelectContent>
          </Select>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" className="gap-1.5 rounded-lg" disabled={busy}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                Download report
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {TABLE_FORMATS.map((f) => (
                <DropdownMenuItem key={f} onClick={() => downloadReport(f)}>
                  Summary as {FORMAT_LABEL[f]}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" className="gap-1.5 rounded-lg" disabled={busy}>
                <Download className="h-4 w-4" />
                Download all data
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {TABLE_FORMATS.map((f) => (
                <DropdownMenuItem key={f} onClick={() => downloadAllData(f)}>
                  All fields as {FORMAT_LABEL[f]}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {/* Filters */}
      <div className="flex-shrink-0 px-6 py-3 border-b flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">From</span>
          <Input
            type="date"
            value={start}
            max={end || undefined}
            onChange={(e) => resetToFirstPage(setStart)(e.target.value)}
            className="h-9 w-40 rounded-lg"
          />
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">To</span>
          <Input
            type="date"
            value={end}
            min={start || undefined}
            onChange={(e) => resetToFirstPage(setEnd)(e.target.value)}
            className="h-9 w-40 rounded-lg"
          />
        </div>
        <Select value={statusFilter} onValueChange={resetToFirstPage(setStatusFilter)}>
          <SelectTrigger className="w-44 h-9 rounded-lg">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            {STATUS_OPTIONS.map((s) => (
              <SelectItem key={s} value={s}>
                {s === "all" ? "All statuses" : s.replace(/_/g, " ")}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {/* Field configuration for "Download all data": a selection switches that export to the
            config-driven per-document-row layout (its schema = columns, filtered to that type). */}
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Field config</span>
          <Select value={configId} onValueChange={resetToFirstPage(setConfigId)}>
            <SelectTrigger className="w-52 h-9 rounded-lg">
              <SelectValue placeholder="Field configuration" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All configurations</SelectItem>
              {configs.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.name}
                  {c.is_template ? " (template)" : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Input
          placeholder="Document type"
          value={typeFilter}
          onChange={(e) => resetToFirstPage(setTypeFilter)(e.target.value)}
          className="h-9 w-44 rounded-lg"
        />
        <div className="ml-auto text-xs text-muted-foreground">
          {total} document{total === 1 ? "" : "s"}
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto px-6 py-5">
        {isLoading ? (
          <div className="flex items-center justify-center py-20 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin mr-2" /> Loading report…
          </div>
        ) : isError ? (
          <div className="flex items-center justify-center py-20 text-red-600">
            Could not load the report. Please try again.
          </div>
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
            <FileText className="h-8 w-8 mb-2 opacity-40" />
            No processed documents match these filters.
          </div>
        ) : (
          <div className="rounded-xl border overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/30 hover:bg-muted/30">
                  <TableHead className="font-semibold text-xs uppercase tracking-wide">Document</TableHead>
                  <TableHead className="font-semibold text-xs uppercase tracking-wide w-32">Type</TableHead>
                  <TableHead className="font-semibold text-xs uppercase tracking-wide w-32">Uploaded</TableHead>
                  <TableHead className="font-semibold text-xs uppercase tracking-wide w-40">Processed</TableHead>
                  <TableHead className="font-semibold text-xs uppercase tracking-wide w-20 text-center">Conf.</TableHead>
                  <TableHead className="font-semibold text-xs uppercase tracking-wide w-24">Fields</TableHead>
                  <TableHead className="font-semibold text-xs uppercase tracking-wide w-36">Status</TableHead>
                  <TableHead className="w-28 text-right" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.document_id} className="hover:bg-muted/20 transition-colors">
                    <TableCell>
                      <div className="flex items-center gap-2.5">
                        <div className="h-7 w-7 rounded-lg bg-[#D04A02]/8 flex items-center justify-center flex-shrink-0">
                          <FileText className="h-3.5 w-3.5 text-[#D04A02]/60" />
                        </div>
                        <span className="font-medium text-sm break-all">{row.original_filename}</span>
                      </div>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">{row.predicted_type || "—"}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{fmtDate(row.uploaded_at)}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{fmtDateTime(row.pipeline_completed_at)}</TableCell>
                    <TableCell className="text-sm text-center text-muted-foreground">
                      {row.overall_confidence != null ? `${row.overall_confidence}%` : "—"}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {row.header_count} hdr · {row.line_item_count} row{row.line_item_count === 1 ? "" : "s"}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col gap-0.5">
                        <Badge variant="outline" className={`w-fit ${statusClass(row.status)}`}>
                          {row.status.replace(/_/g, " ")}
                        </Badge>
                        {row.review_final_status && (
                          <span className="text-[11px] text-muted-foreground">
                            audited · {row.review_final_status.replace(/_/g, " ")}
                          </span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="sm" className="h-8 gap-1 rounded-lg" disabled={busy}>
                              <Download className="h-3.5 w-3.5" /> Export
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            {DOC_FORMATS.map((f) => (
                              <DropdownMenuItem key={f} onClick={() => exportDoc(row, f)}>
                                {FORMAT_LABEL[f]}
                              </DropdownMenuItem>
                            ))}
                          </DropdownMenuContent>
                        </DropdownMenu>
                        {row.has_log && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-8 w-8 p-0 rounded-lg"
                            title="Download processing log"
                            disabled={busy}
                            onClick={() => downloadLog(row)}
                          >
                            <ScrollText className="h-3.5 w-3.5" />
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>

      {/* Pagination */}
      {!isLoading && !isError && total > 0 && (
        <div className="flex-shrink-0 border-t px-6 py-3 flex items-center justify-between">
          <span className="text-xs text-muted-foreground">
            Page {page} of {totalPages} · {total} total
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="h-8 gap-1 rounded-lg"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              <ChevronLeft className="h-4 w-4" /> Prev
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-8 gap-1 rounded-lg"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
