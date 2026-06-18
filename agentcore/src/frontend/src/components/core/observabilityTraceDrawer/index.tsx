import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  X,
  ChevronRight,
  ChevronDown,
  Clock,
  Coins,
  Cpu,
  Layers,
  CheckCircle2,
  AlertCircle,
  Activity,
  FileCode,
  ExternalLink
} from "lucide-react";
import { cn } from "@/utils/utils";
import { useGetObservabilityTraceDetail } from "@/controllers/API/queries/observability/use-get-observability-trace-detail";
import type { ObservationResponse } from "@/controllers/API/queries/observability/types";

interface ObservabilityTraceDrawerProps {
  traceId: string | null;
  isOpen: boolean;
  onClose: () => void;
  langfuseBaseUrl?: string; // Optional custom Langfuse console url (falls back to http://localhost:3001)
}

export function ObservabilityTraceDrawer({
  traceId,
  isOpen,
  onClose,
  langfuseBaseUrl = "http://localhost:3001"
}: ObservabilityTraceDrawerProps) {
  const { data: traceDetail, isLoading, error } = useGetObservabilityTraceDetail(
    { trace_id: traceId || "" },
    { enabled: !!traceId && isOpen }
  );

  // Tracks which observations have their I/O details expanded
  const [expandedObservations, setExpandedObservations] = React.useState<Record<string, boolean>>({});

  // Reset expanded states when a new trace is loaded
  React.useEffect(() => {
    setExpandedObservations({});
  }, [traceId]);

  const toggleExpand = (id: string) => {
    setExpandedObservations((prev) => ({
      ...prev,
      [id]: !prev[id],
    }));
  };

  // Reconstruct parent-child depth map to draw hierarchical tree indentations
  const depthMap = React.useMemo(() => {
    if (!traceDetail?.observations) return {};
    
    const map: Record<string, number> = {};
    const parentMap: Record<string, string> = {};
    
    traceDetail.observations.forEach((o) => {
      if (o.parent_observation_id) {
        parentMap[o.id] = o.parent_observation_id;
      }
    });

    const getDepth = (id: string): number => {
      if (map[id] !== undefined) return map[id];
      const parentId = parentMap[id];
      if (!parentId || parentId === id) {
        map[id] = 0;
        return 0;
      }
      const depth = getDepth(parentId) + 1;
      map[id] = depth;
      return depth;
    };

    traceDetail.observations.forEach((o) => {
      getDepth(o.id);
    });

    return map;
  }, [traceDetail]);

  const renderPayload = (val: any) => {
    if (val == null) return <span className="text-muted-foreground italic">None</span>;
    if (typeof val === "string") {
      try {
        const parsed = JSON.parse(val);
        return <pre className="font-mono text-xxs leading-relaxed whitespace-pre-wrap">{JSON.stringify(parsed, null, 2)}</pre>;
      } catch {
        return <p className="font-sans text-xxs whitespace-pre-wrap leading-relaxed">{val}</p>;
      }
    }
    return <pre className="font-mono text-xxs leading-relaxed whitespace-pre-wrap">{JSON.stringify(val, null, 2)}</pre>;
  };

  // Format latency helper
  const formatDuration = (ms: number | null | undefined) => {
    if (ms == null) return "N/A";
    if (ms < 1000) return `${ms.toFixed(0)}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  };

  // Resolve status color and icon
  const getStatusDetails = (o: ObservationResponse) => {
    const isError = o.level === "ERROR" || (o.status_message && o.status_message.toLowerCase().includes("fail"));
    const isWarning = o.level === "WARNING" || o.level === "DEBUG";
    
    if (isError) {
      return {
        icon: <AlertCircle className="h-4 w-4 text-red-500 fill-red-100 dark:fill-transparent" />,
        dotClass: "bg-red-500",
        bgClass: "border-red-200 bg-red-50/20 dark:border-red-900/30 dark:bg-red-950/10",
        label: "Error"
      };
    }
    if (isWarning) {
      return {
        icon: <AlertCircle className="h-4 w-4 text-amber-500 fill-amber-100 dark:fill-transparent" />,
        dotClass: "bg-amber-500",
        bgClass: "border-amber-200 bg-amber-50/20 dark:border-amber-900/30 dark:bg-amber-950/10",
        label: "Warning"
      };
    }
    return {
      icon: <CheckCircle2 className="h-4 w-4 text-emerald-500 fill-emerald-100 dark:fill-transparent" />,
      dotClass: "bg-emerald-500",
      bgClass: "border-border bg-card",
      label: "Success"
    };
  };

  return (
    <DialogPrimitive.Root open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogPrimitive.Portal>
        {/* Overlay with glassmorphism backdrop */}
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[3px] transition-opacity duration-300 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        
        {/* Sliding Panel from right */}
        <DialogPrimitive.Content className="fixed inset-y-0 right-0 z-50 flex h-full w-full max-w-xl flex-col border-l border-border bg-[#F7F5F3] dark:bg-zinc-950 shadow-2xl transition-transform duration-300 ease-in-out data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right sm:max-w-3xl">
          
          {/* Header */}
          <div className="flex items-start justify-between border-b border-border bg-white dark:bg-zinc-900 px-6 py-5">
            <div className="min-w-0 pr-8">
              <div className="flex items-center gap-2">
                <DialogPrimitive.Title className="text-base font-bold text-[#2D2926] dark:text-zinc-100 truncate">
                  {traceDetail?.name || "Execution Trace Details"}
                </DialogPrimitive.Title>
                {traceDetail?.level === "ERROR" && (
                  <span className="rounded-full bg-red-100 dark:bg-red-950/50 px-2 py-0.5 text-xxs font-semibold text-red-700 dark:text-red-400">
                    Failed
                  </span>
                )}
              </div>
              <p className="mt-1 font-mono text-xxs text-muted-foreground select-all truncate">
                ID: {traceId}
              </p>
            </div>
            
            <DialogPrimitive.Close className="rounded-full p-1.5 hover:bg-stone-100 dark:hover:bg-zinc-800 transition-colors">
              <X className="h-4 w-4 text-muted-foreground" />
              <span className="sr-only">Close</span>
            </DialogPrimitive.Close>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto px-6 py-5">
            {isLoading && (
              <div className="flex h-64 flex-col items-center justify-center gap-3">
                <Activity className="h-8 w-8 animate-pulse text-[#D04A02]" />
                <p className="text-xs text-muted-foreground">Fetching telemetry nodes from ClickHouse...</p>
              </div>
            )}

            {error && (
              <div className="rounded-xl border border-red-200 bg-red-50/50 dark:bg-red-950/15 dark:border-red-900/30 p-4 text-center">
                <AlertCircle className="mx-auto h-8 w-8 text-red-500" />
                <p className="mt-2 text-sm font-semibold text-red-800 dark:text-red-400">Failed to load trace</p>
                <p className="mt-1 text-xs text-red-600 dark:text-red-500">{(error as any)?.message || "Internal error occurred"}</p>
              </div>
            )}

            {!isLoading && !error && traceDetail && (
              <div className="space-y-6">
                
                {/* 1. Aggregated Metrics KPI Cards */}
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  
                  {/* Latency */}
                  <div className="rounded-xl border border-border bg-white dark:bg-zinc-900 p-3 shadow-sm">
                    <div className="flex items-center gap-1.5 text-xxs uppercase tracking-wider text-muted-foreground">
                      <Clock className="h-3 w-3 text-[#D04A02]" />
                      Latency
                    </div>
                    <p className="mt-1 text-lg font-bold text-foreground">
                      {formatDuration(traceDetail.latency_ms)}
                    </p>
                  </div>

                  {/* Total Cost */}
                  <div className="rounded-xl border border-border bg-white dark:bg-zinc-900 p-3 shadow-sm">
                    <div className="flex items-center gap-1.5 text-xxs uppercase tracking-wider text-muted-foreground">
                      <Coins className="h-3 w-3 text-[#D04A02]" />
                      Total Cost
                    </div>
                    <p className="mt-1 text-lg font-bold text-foreground">
                      ${traceDetail.total_cost.toFixed(5)}
                    </p>
                  </div>

                  {/* Total Tokens */}
                  <div className="rounded-xl border border-border bg-white dark:bg-zinc-900 p-3 shadow-sm">
                    <div className="flex items-center gap-1.5 text-xxs uppercase tracking-wider text-muted-foreground">
                      <Layers className="h-3 w-3 text-[#D04A02]" />
                      Total Tokens
                    </div>
                    <p className="mt-1 text-lg font-bold text-foreground">
                      {traceDetail.total_tokens.toLocaleString()}
                    </p>
                    <span className="text-[10px] text-muted-foreground">
                      in: {traceDetail.input_tokens} / out: {traceDetail.output_tokens}
                    </span>
                  </div>

                  {/* Models used */}
                  <div className="rounded-xl border border-border bg-white dark:bg-zinc-900 p-3 shadow-sm">
                    <div className="flex items-center gap-1.5 text-xxs uppercase tracking-wider text-muted-foreground">
                      <Cpu className="h-3 w-3 text-[#D04A02]" />
                      Models
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {traceDetail.models_used.length > 0 ? (
                        traceDetail.models_used.map((model) => (
                          <span
                            key={model}
                            className="rounded bg-stone-100 dark:bg-zinc-800 px-1.5 py-0.5 text-[10px] font-mono text-foreground leading-tight"
                          >
                            {model}
                          </span>
                        ))
                      ) : (
                        <span className="text-xs text-muted-foreground italic">None used</span>
                      )}
                    </div>
                  </div>

                </div>

                {/* 2. Deep Link to Langfuse Native Console */}
                <div className="flex items-center justify-between rounded-xl border border-[#D04A02]/20 bg-orange-50/30 dark:bg-orange-950/10 px-4 py-3.5 shadow-sm">
                  <div className="min-w-0 pr-4">
                    <p className="text-xs font-semibold text-[#2D2926] dark:text-zinc-200">
                      View full pipeline dashboard inside Langfuse
                    </p>
                    <p className="text-[10px] text-muted-foreground mt-0.5 truncate">
                      Inspect complete trace tree, raw database logs, and model response metrics natively.
                    </p>
                  </div>
                  <a
                    href={traceDetail.langfuse_console_url || `${langfuseBaseUrl}/project/default/traces/${traceDetail.id}`}
                    target="_blank"
                    rel="noreferrer"
                    className="flex shrink-0 items-center gap-1.5 rounded-lg bg-[#D04A02] hover:bg-[#b03d01] px-3.5 py-1.5 text-xs font-semibold text-white transition-all shadow-sm active:scale-95"
                  >
                    Open Console
                    <ExternalLink className="h-3 w-3" />
                  </a>
                </div>

                {/* 3. Raw Trace Inputs and Outputs */}
                {(traceDetail.input || traceDetail.output) && (
                  <div className="rounded-xl border border-border bg-white dark:bg-zinc-900 overflow-hidden shadow-sm">
                    <div className="flex items-center gap-2 border-b border-border bg-stone-50/60 dark:bg-zinc-800/40 px-4 py-2 text-xxs font-bold uppercase tracking-wider text-muted-foreground">
                      <FileCode className="h-3.5 w-3.5 text-[#D04A02]" />
                      Global Trace Payload
                    </div>
                    <div className="p-4 space-y-3">
                      {traceDetail.input && (
                        <div>
                          <p className="text-xxs font-bold text-sky-600 dark:text-sky-400 uppercase tracking-wider mb-1">Input Data</p>
                          <div className="max-h-40 overflow-y-auto rounded-lg bg-stone-50 dark:bg-zinc-950 border border-border/60 p-2.5">
                            {renderPayload(traceDetail.input)}
                          </div>
                        </div>
                      )}
                      {traceDetail.output && (
                        <div>
                          <p className="text-xxs font-bold text-emerald-600 dark:text-emerald-400 uppercase tracking-wider mb-1">Output Response</p>
                          <div className="max-h-40 overflow-y-auto rounded-lg bg-stone-50 dark:bg-zinc-950 border border-border/60 p-2.5">
                            {renderPayload(traceDetail.output)}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* 4. Hierarchical Timeline tree */}
                <div className="space-y-3">
                  <p className="text-xxs font-bold uppercase tracking-widest text-muted-foreground">
                    Execution Timeline Steps ({traceDetail.observations.length})
                  </p>

                  <div className="relative border-l border-stone-200 dark:border-zinc-800 pl-4 ml-2.5 space-y-4">
                    {traceDetail.observations.length > 0 ? (
                      traceDetail.observations.map((obs) => {
                        const depth = depthMap[obs.id] || 0;
                        const isExpanded = !!expandedObservations[obs.id];
                        const { icon, dotClass, bgClass, label: statusLabel } = getStatusDetails(obs);
                        const isGeneration = obs.type === "GENERATION";

                        return (
                          <div
                            key={obs.id}
                            className="relative group transition-all duration-150"
                            style={{ marginLeft: `${depth * 14}px` }}
                          >
                            {/* Horizontal thread connector from hierarchy timeline */}
                            {depth > 0 && (
                              <div
                                className="absolute top-4 border-t border-dashed border-stone-300 dark:border-zinc-800"
                                style={{
                                  left: `-${depth * 14 + 17}px`,
                                  width: `${depth * 14 + 17}px`,
                                }}
                              />
                            )}

                            {/* Circular Timeline Dot */}
                            <div className="absolute -left-[25px] top-3 flex h-4 w-4 items-center justify-center">
                              <span className={cn("h-2 w-2 rounded-full ring-4 ring-white dark:ring-zinc-950", dotClass)} />
                            </div>

                            {/* Node Card */}
                            <div className={cn("rounded-xl border shadow-sm transition-shadow hover:shadow-md", bgClass)}>
                              
                              {/* Summary Line */}
                              <button
                                type="button"
                                onClick={() => toggleExpand(obs.id)}
                                className="flex w-full items-center justify-between px-4 py-3 text-left min-w-0"
                              >
                                <div className="flex items-center gap-2.5 min-w-0">
                                  {icon}
                                  <div className="min-w-0">
                                    <div className="flex flex-wrap items-center gap-1.5">
                                      <span className="font-sans text-xs font-bold text-foreground">
                                        {obs.name || obs.type || "node"}
                                      </span>
                                      <span className={cn(
                                        "rounded px-1 py-0.5 text-[9px] font-bold uppercase tracking-wide leading-none",
                                        isGeneration
                                          ? "bg-amber-100 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300"
                                          : "bg-stone-100 text-stone-700 dark:bg-zinc-800 dark:text-zinc-300"
                                      )}>
                                        {obs.type}
                                      </span>
                                      {obs.model && (
                                        <span className="rounded font-mono text-[9px] text-muted-foreground bg-stone-50 dark:bg-zinc-900 border border-border px-1">
                                          {obs.model}
                                        </span>
                                      )}
                                    </div>
                                  </div>
                                </div>

                                <div className="flex shrink-0 items-center gap-3 ml-4">
                                  <div className="flex items-center gap-1.5 text-xxs text-muted-foreground font-medium">
                                    <span>{formatDuration(obs.latency_ms)}</span>
                                    {obs.total_tokens > 0 && (
                                      <>
                                        <span>•</span>
                                        <span>{obs.total_tokens} tokens</span>
                                      </>
                                    )}
                                  </div>
                                  {isExpanded ? (
                                    <ChevronDown className="h-4 w-4 text-muted-foreground" />
                                  ) : (
                                    <ChevronRight className="h-4 w-4 text-muted-foreground" />
                                  )}
                                </div>
                              </button>

                              {/* Expanded Parameters Panel */}
                              {isExpanded && (
                                <div className="border-t border-border/60 bg-stone-50/40 dark:bg-zinc-900/10 px-4 pb-4 pt-3 space-y-3">
                                  
                                  {/* Error message alert */}
                                  {obs.status_message && (
                                    <div className="rounded-lg border border-red-200/60 bg-red-50/40 dark:bg-red-950/10 dark:border-red-900/20 p-2.5 text-xxs text-red-800 dark:text-red-400">
                                      <span className="font-bold uppercase mr-1">Status Message:</span>
                                      {obs.status_message}
                                    </div>
                                  )}

                                  {/* Metrics & Metadata Row */}
                                  <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-[10px] text-muted-foreground border-b border-border/50 pb-2.5">
                                    {obs.start_time && (
                                      <div>
                                        <span className="font-semibold text-foreground">Start: </span>
                                        {new Date(obs.start_time).toLocaleTimeString()}
                                      </div>
                                    )}
                                    {obs.end_time && (
                                      <div>
                                        <span className="font-semibold text-foreground">End: </span>
                                        {new Date(obs.end_time).toLocaleTimeString()}
                                      </div>
                                    )}
                                    {obs.total_cost > 0 && (
                                      <div>
                                        <span className="font-semibold text-foreground">Cost: </span>
                                        ${obs.total_cost.toFixed(6)}
                                      </div>
                                    )}
                                  </div>

                                  {/* Input / Prompt */}
                                  {obs.input && (
                                    <div className="space-y-1">
                                      <span className="text-[10px] font-bold uppercase tracking-wider text-sky-600 dark:text-sky-400">
                                        {isGeneration ? "LLM Input (Prompt)" : "Span Input"}
                                      </span>
                                      <div className="max-h-48 overflow-y-auto rounded-lg border border-border/50 bg-white dark:bg-zinc-950 p-2.5">
                                        {renderPayload(obs.input)}
                                      </div>
                                    </div>
                                  )}

                                  {/* Output / Completion */}
                                  {obs.output && (
                                    <div className="space-y-1">
                                      <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-600 dark:text-emerald-400">
                                        {isGeneration ? "LLM Output (Response)" : "Span Output"}
                                      </span>
                                      <div className="max-h-48 overflow-y-auto rounded-lg border border-border/50 bg-white dark:bg-zinc-950 p-2.5">
                                        {renderPayload(obs.output)}
                                      </div>
                                    </div>
                                  )}

                                  {/* Node metadata */}
                                  {obs.metadata && Object.keys(obs.metadata).length > 0 && (
                                    <div className="space-y-1">
                                      <span className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                                        Execution Context Metadata
                                      </span>
                                      <div className="max-h-36 overflow-y-auto rounded-lg border border-border/50 bg-white dark:bg-zinc-950 p-2.5">
                                        {renderPayload(obs.metadata)}
                                      </div>
                                    </div>
                                  )}

                                </div>
                              )}

                            </div>
                          </div>
                        );
                      })
                    ) : (
                      <div className="py-6 text-center text-xs text-muted-foreground italic">
                        No timeline nodes recorded inside this trace.
                      </div>
                    )}
                  </div>
                </div>

              </div>
            )}
          </div>
          
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
