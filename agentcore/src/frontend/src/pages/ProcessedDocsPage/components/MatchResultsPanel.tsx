import { useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw, CheckCircle, XCircle, AlertTriangle, Circle } from "lucide-react";
import type { MatchResult, MatchDiscrepancy } from "@/controllers/API/queries/idp/use-get-match-results";
import { useAcceptDiscrepancy, useRejectDiscrepancy } from "@/controllers/API/queries/idp";

// ─────────────────────── Status badge ────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    full_match: "bg-green-100 text-green-700 border-green-200",
    partial_match: "bg-amber-100 text-amber-700 border-amber-200",
    mismatch: "bg-red-100 text-red-700 border-red-200",
    error: "bg-zinc-100 text-zinc-600 border-zinc-200",
  };
  const labels: Record<string, string> = {
    full_match: "Full Match",
    partial_match: "Partial Match",
    mismatch: "Mismatch",
    error: "Error",
  };
  return (
    <span
      className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${colors[status] ?? colors.error}`}
    >
      {labels[status] ?? status}
    </span>
  );
}

// ─────────────────────── Row icon ────────────────────────────────────────────

function RowIcon({ status, isAccepted }: { status: string; isAccepted: boolean }) {
  if (isAccepted) return <CheckCircle size={14} className="text-green-500 shrink-0" />;
  if (status === "full_match") return <CheckCircle size={14} className="text-green-500 shrink-0" />;
  if (status === "partial_match") return <AlertTriangle size={14} className="text-amber-500 shrink-0" />;
  if (status === "mismatch") return <XCircle size={14} className="text-red-500 shrink-0" />;
  return <Circle size={14} className="text-zinc-400 shrink-0" />;
}

// ─────────────────────── Accept / Reject controls ────────────────────────────

function DiscrepancyActions({
  disc,
  documentId,
}: {
  disc: MatchDiscrepancy;
  documentId: string;
}) {
  const { t } = useTranslation();
  const { mutate: accept, isPending: accepting } = useAcceptDiscrepancy(documentId);
  const { mutate: reject, isPending: rejecting } = useRejectDiscrepancy(documentId);
  const [note, setNote] = useState("");

  if (disc.status === "full_match") return null;

  if (disc.is_accepted) {
    return (
      <div className="flex items-center gap-1">
        <span className="text-xs text-green-600 font-medium">Accepted</span>
        <button
          onClick={() => reject({ discrepancyId: disc.id, reviewer_note: note })}
          disabled={rejecting}
          className="text-xs text-zinc-500 hover:text-red-600 underline ml-1"
        >
          {t("match.undo", "Undo")}
        </button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1.5">
      <input
        type="text"
        className="h-6 text-xs rounded border border-zinc-200 px-1.5 w-28"
        placeholder={t("match.noteOptional", "Note (optional)")}
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <button
        onClick={() => accept({ discrepancyId: disc.id, reviewer_note: note })}
        disabled={accepting}
        className="h-6 px-2 text-xs rounded bg-green-100 text-green-700 hover:bg-green-200 font-medium"
      >
        {t("match.accept", "Accept")}
      </button>
    </div>
  );
}

// ─────────────────────── Main panel ──────────────────────────────────────────

interface Props {
  documentId: string;
  matchResult: MatchResult | null | undefined;
  isLoading: boolean;
  onTriggerMatch: () => void;
}

export function MatchResultsPanel({ documentId, matchResult, isLoading, onTriggerMatch }: Props) {
  const { t } = useTranslation();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-10 text-sm text-zinc-500">
        <RefreshCw size={14} className="animate-spin mr-2" />
        {t("match.loading", "Loading match results…")}
      </div>
    );
  }

  if (!matchResult?.has_match) {
    return (
      <div className="flex flex-col items-center gap-3 py-10">
        <p className="text-sm text-zinc-500">
          {t("match.noMatch", "No match results yet. Use the Run Match button to compare this invoice against SAP.")}
        </p>
      </div>
    );
  }

  const discrepancies = matchResult.discrepancies ?? [];
  const headerDiscs = discrepancies.filter((d) => d.scope === "header");
  const lineDiscs = discrepancies.filter((d) => d.scope === "line_item");
  const is3way = matchResult.match_type === "3way";

  return (
    <div className="flex flex-col gap-4">
      {/* Summary bar */}
      <div className="flex items-center justify-between gap-2 rounded-lg border border-zinc-200 bg-zinc-50 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-zinc-600 uppercase tracking-wide">
            {matchResult.match_type === "2way"
              ? t("match.twoWay", "2-Way Match")
              : t("match.threeWay", "3-Way Match")}
          </span>
          {matchResult.overall_status && <StatusBadge status={matchResult.overall_status} />}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-zinc-500">
            {t("match.score", "Score")}: <strong>{matchResult.match_score?.toFixed(1)}%</strong>
          </span>
          {matchResult.po_number && (
            <span className="text-xs text-zinc-500">
              PO: <strong>{matchResult.po_number}</strong>
            </span>
          )}
          {is3way && matchResult.gr_document_number && (
            <span className="text-xs text-zinc-500">
              GR: <strong>{matchResult.gr_document_number}</strong>
            </span>
          )}
          <button
            onClick={onTriggerMatch}
            className="flex items-center gap-1 text-xs text-primary hover:underline"
          >
            <RefreshCw size={12} />
            {t("match.rerun", "Re-run")}
          </button>
        </div>
      </div>

      {/* Discrepancy table */}
      <div className="overflow-x-auto rounded-lg border border-zinc-200">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-zinc-50 border-b border-zinc-200">
              <th className="px-3 py-2 text-left font-medium text-zinc-600 w-5"></th>
              <th className="px-3 py-2 text-left font-medium text-zinc-600">
                {t("match.field", "Field")}
              </th>
              <th className="px-3 py-2 text-left font-medium text-zinc-600">
                {t("match.invoiceValue", "Invoice")}
              </th>
              <th className="px-3 py-2 text-left font-medium text-zinc-600">
                {t("match.poValue", "SAP PO")}
              </th>
              {is3way && (
                <th className="px-3 py-2 text-left font-medium text-zinc-600">
                  {t("match.grValue", "SAP GR")}
                </th>
              )}
              <th className="px-3 py-2 text-left font-medium text-zinc-600">
                {t("match.variance", "Variance")}
              </th>
              <th className="px-3 py-2 text-left font-medium text-zinc-600">
                {t("match.action", "Action")}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100">
            {headerDiscs.length > 0 && (
              <tr>
                <td
                  colSpan={is3way ? 7 : 6}
                  className="px-3 py-1.5 text-xs font-semibold text-zinc-500 bg-zinc-50"
                >
                  {t("match.headerFields", "Header Fields")}
                </td>
              </tr>
            )}
            {headerDiscs.map((disc) => (
              <DiscrepancyRow key={disc.id} disc={disc} documentId={documentId} is3way={is3way} />
            ))}

            {lineDiscs.length > 0 && (
              <tr>
                <td
                  colSpan={is3way ? 7 : 6}
                  className="px-3 py-1.5 text-xs font-semibold text-zinc-500 bg-zinc-50"
                >
                  {t("match.lineItems", "Line Items")}
                </td>
              </tr>
            )}
            {lineDiscs.map((disc) => (
              <DiscrepancyRow key={disc.id} disc={disc} documentId={documentId} is3way={is3way} />
            ))}

            {discrepancies.length === 0 && (
              <tr>
                <td
                  colSpan={is3way ? 7 : 6}
                  className="px-3 py-6 text-center text-zinc-400"
                >
                  {t("match.noDiscrepancies", "No fields compared yet")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DiscrepancyRow({
  disc,
  documentId,
  is3way,
}: {
  disc: MatchDiscrepancy;
  documentId: string;
  is3way: boolean;
}) {
  const rowBg =
    disc.is_accepted
      ? "bg-green-50/40"
      : disc.status === "full_match"
        ? ""
        : disc.status === "partial_match"
          ? "bg-amber-50/40"
          : "bg-red-50/40";

  return (
    <tr className={rowBg}>
      <td className="px-3 py-2">
        <RowIcon status={disc.status} isAccepted={disc.is_accepted} />
      </td>
      <td className="px-3 py-2 font-mono text-zinc-700 whitespace-nowrap">
        {disc.field_name}
      </td>
      <td className="px-3 py-2 text-zinc-800">{disc.invoice_value ?? "—"}</td>
      <td className="px-3 py-2 text-zinc-600">{disc.po_value ?? "—"}</td>
      {is3way && (
        <td className="px-3 py-2 text-zinc-600">{disc.gr_value ?? "—"}</td>
      )}
      <td className="px-3 py-2 text-zinc-500">
        {disc.variance_pct != null ? `${disc.variance_pct.toFixed(2)}%` : "—"}
      </td>
      <td className="px-3 py-2">
        <DiscrepancyActions disc={disc} documentId={documentId} />
      </td>
    </tr>
  );
}
