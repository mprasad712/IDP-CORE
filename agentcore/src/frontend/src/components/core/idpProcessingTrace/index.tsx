import { cn } from "@/utils/utils";
import { useGetDocumentLog } from "@/controllers/API/queries/idp/use-get-document-log";

/**
 * Per-component processing trace for an IDP document — the complete cycle recorded on the latest
 * processing job: input received → config → OCR/native text → classify → extract (what the LLM
 * returned) → route → finalize. Each step shows its status, detail, and the full input/output
 * payload (which config/component, what went in, what came out). Data: GET /documents/{id}/log.
 */
export function ProcessingTrace({ docId }: { docId: string }) {
  const { data, isLoading, isError } = useGetDocumentLog(docId);

  if (isLoading) return <div className="px-4 py-3 text-xs text-muted-foreground">Loading trace…</div>;
  if (isError)   return <div className="px-4 py-3 text-xs text-muted-foreground">Could not load the trace.</div>;

  const steps = data?.log ?? [];
  if (steps.length === 0) return <div className="px-4 py-3 text-xs text-muted-foreground">No trace recorded for this document.</div>;

  return (
    <div className="px-4 py-3 space-y-2.5">
      {steps.map((s, i) => (
        <div key={i} className="text-xs">
          <div className="flex items-start gap-2">
            <span className={cn(
              "mt-1 inline-block h-1.5 w-1.5 rounded-full flex-shrink-0",
              s.status === "ok" ? "bg-emerald-500" : s.status === "warn" ? "bg-amber-500" : "bg-red-500",
            )} />
            <span className="font-mono font-semibold w-20 flex-shrink-0">{s.step}</span>
            <span className="text-muted-foreground">{s.detail}</span>
          </div>
          {s.io?.input != null && (
            <div className="ml-[5.5rem] mt-1">
              <span className="text-[9px] font-bold uppercase tracking-wider text-sky-600/70">Input</span>
              <pre className="max-h-48 overflow-auto rounded bg-sky-500/5 p-2 text-[10px] leading-relaxed text-muted-foreground whitespace-pre-wrap break-all">
                {JSON.stringify(s.io.input, null, 1)}
              </pre>
            </div>
          )}
          {s.io?.output != null && (
            <div className="ml-[5.5rem] mt-1">
              <span className="text-[9px] font-bold uppercase tracking-wider text-emerald-600/70">Output</span>
              <pre className="max-h-48 overflow-auto rounded bg-emerald-500/5 p-2 text-[10px] leading-relaxed text-muted-foreground whitespace-pre-wrap break-all">
                {JSON.stringify(s.io.output, null, 1)}
              </pre>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
