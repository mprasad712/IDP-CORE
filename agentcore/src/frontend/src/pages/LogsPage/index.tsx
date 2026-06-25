import { useState, useEffect } from "react";
import { Search, FileText, Wifi } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/utils/utils";
import { useGetDocumentLogs } from "@/controllers/API/queries/idp";

const PAGE_SIZE = 50;
const POLL_INTERVAL_MS = 5000;

const STATUS_META: Record<string, { label: string; className: string }> = {
  queued:         { label: "Queued",         className: "bg-slate-100 text-slate-700 border-slate-300" },
  processing:     { label: "Processing",     className: "bg-blue-100 text-blue-700 border-blue-300" },
  extracted:      { label: "Extracted",      className: "bg-indigo-100 text-indigo-700 border-indigo-300" },
  pending_review: { label: "Pending Review", className: "bg-amber-100 text-amber-700 border-amber-300" },
  auto_approved:  { label: "Auto Approved",  className: "bg-green-100 text-green-700 border-green-300" },
  reviewed:       { label: "Reviewed",       className: "bg-teal-100 text-teal-700 border-teal-300" },
  failed:         { label: "Failed",         className: "bg-red-100 text-red-700 border-red-300" },
  split:          { label: "Split",          className: "bg-orange-100 text-orange-700 border-orange-300" },
  skipped:        { label: "Skipped",        className: "bg-gray-100 text-gray-500 border-gray-300" },
};

function StatusBadge({ status }: { status: string }) {
  const meta = STATUS_META[status] ?? { label: status, className: "bg-slate-100 text-slate-600 border-slate-300" };
  const isActive = status === "queued" || status === "processing";
  return (
    <Badge variant="outline" className={cn("text-xs font-medium", meta.className)}>
      {isActive && (
        <span className="mr-1.5 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
      )}
      {meta.label}
    </Badge>
  );
}

function formatReceived(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function useLastRefreshedLabel(dataUpdatedAt: number) {
  const [label, setLabel] = useState("Just now");

  useEffect(() => {
    if (!dataUpdatedAt) return;
    setLabel("Just now");
    const id = setInterval(() => {
      const secs = Math.floor((Date.now() - dataUpdatedAt) / 1000);
      if (secs < 5) setLabel("Just now");
      else if (secs < 60) setLabel(`${secs}s ago`);
      else setLabel(`${Math.floor(secs / 60)}m ago`);
    }, 1000);
    return () => clearInterval(id);
  }, [dataUpdatedAt]);

  return label;
}

export default function LogsPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");

  const { data, isLoading, isError, dataUpdatedAt } = useGetDocumentLogs(
    { page, size: PAGE_SIZE },
    { refetchOnMount: true, refetchInterval: POLL_INTERVAL_MS },
  );

  const lastRefreshed = useLastRefreshedLabel(dataUpdatedAt ?? 0);

  const docs = data?.items ?? [];
  const totalPages = data?.pages ?? 1;

  const filtered = search.trim()
    ? docs.filter((d) =>
        d.original_filename.toLowerCase().includes(search.trim().toLowerCase()),
      )
    : docs;

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-foreground">Document Logs</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            All received documents and their current processing status
          </p>
        </div>

        <div className="flex items-center gap-4">
          {/* Live indicator */}
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Wifi className="h-3.5 w-3.5 text-green-500" />
            <span className="text-green-600 font-medium">Live</span>
            <span className="text-muted-foreground">· refreshed {lastRefreshed}</span>
          </div>

          {data && (
            <span className="text-sm text-muted-foreground">
              {data.total} document{data.total !== 1 ? "s" : ""}
            </span>
          )}
        </div>
      </div>

      {/* Search */}
      <div className="relative w-72">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          placeholder="Search by file name…"
          className="pl-9"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
        />
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/40">
              <TableHead className="w-10 pl-4">#</TableHead>
              <TableHead>File Name</TableHead>
              <TableHead className="w-52">Received</TableHead>
              <TableHead className="w-44">Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading && (
              <TableRow>
                <TableCell colSpan={4} className="py-12 text-center text-muted-foreground">
                  Loading…
                </TableCell>
              </TableRow>
            )}
            {isError && (
              <TableRow>
                <TableCell colSpan={4} className="py-12 text-center text-destructive">
                  Failed to load documents. Please try again.
                </TableCell>
              </TableRow>
            )}
            {!isLoading && !isError && filtered.length === 0 && (
              <TableRow>
                <TableCell colSpan={4} className="py-12 text-center text-muted-foreground">
                  <FileText className="mx-auto mb-2 h-8 w-8 opacity-30" />
                  {search ? "No documents match your search." : "No documents found."}
                </TableCell>
              </TableRow>
            )}
            {filtered.map((doc, idx) => (
              <TableRow key={doc.id} className="hover:bg-muted/30">
                <TableCell className="pl-4 text-muted-foreground text-xs">
                  {(page - 1) * PAGE_SIZE + idx + 1}
                </TableCell>
                <TableCell className="max-w-sm">
                  <span
                    className="block truncate font-medium text-sm"
                    title={doc.original_filename}
                  >
                    {doc.original_filename}
                  </span>
                </TableCell>
                <TableCell className="text-sm text-muted-foreground tabular-nums">
                  {formatReceived(doc.created_at)}
                </TableCell>
                <TableCell>
                  <StatusBadge status={doc.status} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between pt-1">
          <span className="text-sm text-muted-foreground">
            Page {page} of {totalPages}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
