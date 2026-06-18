import * as React from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  PieChart,
  Pie,
  Cell
} from "recharts";
import {
  Clock,
  Coins,
  Layers,
  Cpu,
  Search,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Activity,
  Terminal,
  AlertTriangle
} from "lucide-react";
import { api } from "@/controllers/API/api";
import { cn } from "@/utils/utils";
import { ObservabilityTraceDrawer } from "@/components/core/observabilityTraceDrawer";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type {
  MetricsResponse,
  TracesListResponse,
  TraceListItem
} from "@/controllers/API/queries/observability/types";
import { useGetDepartments } from "@/controllers/API/queries/auth";
import type { DepartmentListItem } from "@/controllers/API/queries/auth/use-get-departments";

interface ObservabilityDashboardSectionProps {
  isRootAdmin: boolean;
  isSuperAdmin: boolean;
  isLeaderExecutive: boolean;
  isDepartmentAdmin: boolean;
  isDocApprover: boolean;
  userData: any;
  refreshTick: number;
  accentColor: string;
  viewMode?: "single" | "tabs";
}

const formatDuration = (ms: number | null | undefined) => {
  if (ms == null) return "N/A";
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
};

const chartColors = ["#2563eb", "#14b8a6", "#f97316", "#a855f7", "#ec4899", "#8b5cf6"];

export function ObservabilityDashboardSection({
  isRootAdmin,
  isSuperAdmin,
  isLeaderExecutive,
  isDepartmentAdmin,
  isDocApprover,
  userData,
  refreshTick,
  accentColor,
  viewMode = "single"
}: ObservabilityDashboardSectionProps) {
  // Local Telemetry State
  const [metrics, setMetrics] = React.useState<MetricsResponse | null>(null);
  const [tracesData, setTracesData] = React.useState<TracesListResponse | null>(null);
  const [isLoadingMetrics, setIsLoadingMetrics] = React.useState(true);
  const [isLoadingTraces, setIsLoadingTraces] = React.useState(true);
  const [errorMetrics, setErrorMetrics] = React.useState<string | null>(null);
  const [errorTraces, setErrorTraces] = React.useState<string | null>(null);

  // Search & Pagination States
  const [searchVal, setSearchVal] = React.useState("");
  const [appliedSearch, setAppliedSearch] = React.useState("");
  const [page, setPage] = React.useState(1);
  const limit = 10;
  const [environmentFilter, setEnvironmentFilter] = React.useState<"all" | "uat" | "production">("all");
  const [dateRangeFilter, setDateRangeFilter] = React.useState<"1" | "7" | "30">("30");

  // Selected trace for timeline drawer
  const [selectedTraceId, setSelectedTraceId] = React.useState<string | null>(null);
  const [isDrawerOpen, setIsDrawerOpen] = React.useState(false);

  const dateRangeLabel = React.useMemo(() => {
    if (dateRangeFilter === "1") return "today";
    if (dateRangeFilter === "7") return "last 7d";
    return "last 30d";
  }, [dateRangeFilter]);

  // Determine default scope according to active role privileges
  const defaultScope = React.useMemo(() => {
    if (isSuperAdmin || isLeaderExecutive || isDocApprover) return "all";
    if (isDepartmentAdmin) return "dept";
    return "my";
  }, [isSuperAdmin, isLeaderExecutive, isDocApprover, isDepartmentAdmin]);

  const [traceScopeFilter, setTraceScopeFilter] = React.useState<"all" | "dept" | "my">(defaultScope);
  const [selectedDeptId, setSelectedDeptId] = React.useState<string>("");
  const [departments, setDepartments] = React.useState<DepartmentListItem[]>([]);

  const { mutate: mutateGetDepartments } = useGetDepartments();

  React.useEffect(() => {
    const isPrivileged = isSuperAdmin || isLeaderExecutive || isDocApprover;
    if (isPrivileged) {
      mutateGetDepartments(undefined, {
        onSuccess: (items) => {
          if (Array.isArray(items)) {
            setDepartments(items);
            if (items.length > 0 && !selectedDeptId) {
              setSelectedDeptId(items[0].id);
            }
          }
        }
      });
    }
  }, [isSuperAdmin, isLeaderExecutive, isDocApprover]);

  const tzOffset = React.useMemo(() => -new Date().getTimezoneOffset(), []);

  // Fetch aggregated metrics
  React.useEffect(() => {
    let active = true;
    setIsLoadingMetrics(true);

    const todayStr = new Date().toISOString().slice(0, 10);
    const ago = new Date();
    const daysOffset = dateRangeFilter === "1" ? 0 : Number(dateRangeFilter);
    ago.setDate(ago.getDate() - daysOffset);
    const fromDateStr = ago.toISOString().slice(0, 10);

    const params: Record<string, any> = {
      from_date: fromDateStr,
      to_date: todayStr,
      trace_scope: traceScopeFilter,
      tz_offset: tzOffset
    };
    if (userData?.organization_id) params.org_id = userData.organization_id;
    if (traceScopeFilter === "dept") {
      if ((isSuperAdmin || isLeaderExecutive || isDocApprover) && selectedDeptId) {
        params.dept_id = selectedDeptId;
      } else if (userData?.department_id) {
        params.dept_id = userData.department_id;
      }
    }
    if (environmentFilter !== "all") params.environment = environmentFilter;
    if (appliedSearch) params.search = appliedSearch;

    api.get<MetricsResponse>("/api/observability/metrics", { params })
      .then((res) => {
        if (active) {
          setMetrics(res.data);
          setErrorMetrics(null);
        }
      })
      .catch((err) => {
        if (active) {
          setErrorMetrics(err?.response?.data?.detail || "Failed to fetch observability metrics");
        }
      })
      .finally(() => {
        if (active) setIsLoadingMetrics(false);
      });

    return () => {
      active = false;
    };
  }, [traceScopeFilter, selectedDeptId, userData?.organization_id, userData?.department_id, environmentFilter, dateRangeFilter, appliedSearch, tzOffset, refreshTick, isSuperAdmin, isLeaderExecutive, isDocApprover]);

  // Fetch individual trace logs
  React.useEffect(() => {
    let active = true;
    setIsLoadingTraces(true);

    const params: Record<string, any> = {
      page,
      limit,
      trace_scope: traceScopeFilter
    };
    if (userData?.organization_id) params.org_id = userData.organization_id;
    if (traceScopeFilter === "dept") {
      if ((isSuperAdmin || isLeaderExecutive || isDocApprover) && selectedDeptId) {
        params.dept_id = selectedDeptId;
      } else if (userData?.department_id) {
        params.dept_id = userData.department_id;
      }
    }
    if (environmentFilter !== "all") params.environment = environmentFilter;

    // Fetch dates according to range filter selection
    const todayStr = new Date().toISOString().slice(0, 10);
    const ago = new Date();
    const daysOffset = dateRangeFilter === "1" ? 0 : Number(dateRangeFilter);
    ago.setDate(ago.getDate() - daysOffset);
    const fromDateStr = ago.toISOString().slice(0, 10);

    params.from_date = fromDateStr;
    params.to_date = todayStr;

    api.get<TracesListResponse>("/api/observability/traces", { params })
      .then((res) => {
        if (active) {
          setTracesData(res.data);
          setErrorTraces(null);
        }
      })
      .catch((err) => {
        if (active) {
          setErrorTraces(err?.response?.data?.detail || "Failed to fetch traces list");
        }
      })
      .finally(() => {
        if (active) setIsLoadingTraces(false);
      });

    return () => {
      active = false;
    };
  }, [traceScopeFilter, selectedDeptId, userData?.organization_id, userData?.department_id, environmentFilter, dateRangeFilter, page, refreshTick, isSuperAdmin, isLeaderExecutive, isDocApprover]);

  // Search submit trigger
  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setAppliedSearch(searchVal.trim());
    setPage(1);
  };

  // KPI calculations
  const totalCost = metrics?.total_cost_usd ?? 0;
  const totalTraces = metrics?.total_traces ?? 0;
  const avgLatencySec = metrics?.avg_latency_ms ? (metrics.avg_latency_ms / 1000) : 0;
  const totalTokens = metrics?.total_tokens ?? 0;

  // Chart data mappings
  const dailyTokenData = React.useMemo(() => {
    if (!metrics?.by_date?.length) {
      // Fallback timeline structure
      return Array.from({ length: 7 }, (_, i) => {
        const d = new Date();
        d.setDate(d.getDate() - (6 - i));
        return {
          label: d.toLocaleDateString("en-US", { month: "short", day: "numeric" }),
          Tokens: 0
        };
      });
    }
    return metrics.by_date.map((pt) => ({
      label: new Date(`${pt.date}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
      Tokens: pt.total_tokens
    }));
  }, [metrics]);

  const modelUsageData = React.useMemo(() => {
    if (!metrics?.by_model?.length) return [];
    return metrics.by_model.map((m) => ({
      label: m.model,
      value: m.total_cost > 0 ? parseFloat(m.total_cost.toFixed(5)) : m.call_count
    }));
  }, [metrics]);

  const tracesList = React.useMemo(() => {
    if (!tracesData?.traces) return [];
    
    // Apply local client-side filter if search parameter is set
    if (appliedSearch) {
      const s = appliedSearch.toLowerCase();
      return tracesData.traces.filter((t) => 
        (t.name && t.name.toLowerCase().includes(s)) ||
        (t.id && t.id.toLowerCase().includes(s)) ||
        (t.session_id && t.session_id.toLowerCase().includes(s))
      );
    }
    return tracesData.traces;
  }, [tracesData, appliedSearch]);

  const totalPages = Math.ceil((tracesData?.total ?? 0) / limit) || 1;

  // Render donut charts Custom Tooltip
  const DonutTooltip = ({ active, payload }: any) => {
    if (!active || !payload?.length) return null;
    const isCost = metrics?.by_model?.some(m => m.total_cost > 0);
    return (
      <div className="rounded-lg border border-border bg-card px-3 py-2 text-xs shadow">
        <p className="font-semibold text-foreground">{payload[0].name}</p>
        <p className="text-muted-foreground">
          {isCost ? `Cost: $${payload[0].value.toFixed(5)}` : `Runs: ${payload[0].value}`}
        </p>
      </div>
    );
  };

  const kpisGrid = (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      
      {/* Total Cost */}
      <div className="group relative overflow-hidden rounded-xl border border-border bg-white dark:bg-zinc-900 p-4 shadow-sm transition-all duration-150 hover:-translate-y-0.5 hover:shadow-md">
        <div className="absolute left-0 top-0 bottom-0 w-0.5 rounded-l-xl bg-orange-600" />
        <div className="flex items-center gap-1.5 text-xxs uppercase tracking-widest text-muted-foreground leading-snug font-medium">
          <Coins className="h-3.5 w-3.5 text-orange-600" />
          Total Cost (USD)
        </div>
        <p className="mt-2.5 text-2xl font-bold text-foreground leading-none tracking-tight">
          {isLoadingMetrics ? "..." : `$${totalCost.toFixed(5)}`}
        </p>
      </div>

      {/* Total Runs / Traces */}
      <div className="group relative overflow-hidden rounded-xl border border-border bg-white dark:bg-zinc-900 p-4 shadow-sm transition-all duration-150 hover:-translate-y-0.5 hover:shadow-md">
        <div className="absolute left-0 top-0 bottom-0 w-0.5 rounded-l-xl bg-orange-600" />
        <div className="flex items-center gap-1.5 text-xxs uppercase tracking-widest text-muted-foreground leading-snug font-medium">
          <Activity className="h-3.5 w-3.5 text-orange-600" />
          Total Pipeline Runs
        </div>
        <p className="mt-2.5 text-2xl font-bold text-foreground leading-none tracking-tight">
          {isLoadingMetrics ? "..." : totalTraces.toLocaleString()}
        </p>
      </div>

      {/* Avg Latency */}
      <div className="group relative overflow-hidden rounded-xl border border-border bg-white dark:bg-zinc-900 p-4 shadow-sm transition-all duration-150 hover:-translate-y-0.5 hover:shadow-md">
        <div className="absolute left-0 top-0 bottom-0 w-0.5 rounded-l-xl bg-orange-600" />
        <div className="flex items-center gap-1.5 text-xxs uppercase tracking-widest text-muted-foreground leading-snug font-medium">
          <Clock className="h-3.5 w-3.5 text-orange-600" />
          Avg Latency (Sec)
        </div>
        <p className="mt-2.5 text-2xl font-bold text-foreground leading-none tracking-tight">
          {isLoadingMetrics ? "..." : `${avgLatencySec.toFixed(2)}s`}
        </p>
      </div>

      {/* Total Tokens */}
      <div className="group relative overflow-hidden rounded-xl border border-border bg-white dark:bg-zinc-900 p-4 shadow-sm transition-all duration-150 hover:-translate-y-0.5 hover:shadow-md">
        <div className="absolute left-0 top-0 bottom-0 w-0.5 rounded-l-xl bg-orange-600" />
        <div className="flex items-center gap-1.5 text-xxs uppercase tracking-widest text-muted-foreground leading-snug font-medium">
          <Layers className="h-3.5 w-3.5 text-orange-600" />
          Tokens Consumed
        </div>
        <p className="mt-2.5 text-2xl font-bold text-foreground leading-none tracking-tight">
          {isLoadingMetrics ? "..." : totalTokens.toLocaleString()}
        </p>
        {!isLoadingMetrics && metrics && (
          <span className="text-[10px] text-muted-foreground">
            in: {metrics.input_tokens.toLocaleString()} / out: {metrics.output_tokens.toLocaleString()}
          </span>
        )}
      </div>

    </div>
  );

  const chartsRow = (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      
      {/* Token Area Trend */}
      <div className="overflow-hidden rounded-xl border border-border bg-white dark:bg-zinc-900 shadow-sm">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-orange-50/5 dark:bg-zinc-800/40">
          <div>
            <p className="text-xs font-bold text-foreground leading-tight">Daily Token Consumption</p>
            <p className="text-[10px] text-muted-foreground mt-0.5">Token usage trend ({dateRangeLabel})</p>
          </div>
          <Layers className="h-3.5 w-3.5 text-orange-600" />
        </div>
        <div className="p-4 flex justify-center h-48 w-full">
          {isLoadingMetrics ? (
            <div className="h-full flex items-center justify-center text-xs text-muted-foreground">Loading chart...</div>
          ) : (
            <div className="h-full w-full max-w-lg">
              <AreaChart
                width={340}
                height={170}
                data={dailyTokenData}
                margin={{ top: 8, right: 10, left: -15, bottom: 0 }}
              >
                <defs>
                  <linearGradient id="tokenGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={accentColor} stopOpacity={0.15} />
                    <stop offset="95%" stopColor={accentColor} stopOpacity={0.01} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                <XAxis dataKey="label" tick={{ fontSize: 9, fill: "hsl(var(--muted-foreground))" }} />
                <YAxis tick={{ fontSize: 9, fill: "hsl(var(--muted-foreground))" }} />
                <Tooltip content={({ active, payload, label }: any) => {
                  if (!active || !payload?.length) return null;
                  return (
                    <div className="rounded-lg border border-border bg-card px-3 py-2 text-xs shadow">
                      <p className="font-semibold text-foreground">{label}</p>
                      <p className="text-muted-foreground">Tokens: {payload[0].value.toLocaleString()}</p>
                    </div>
                  );
                }} />
                <Area type="monotone" dataKey="Tokens" stroke={accentColor} strokeWidth={2} fill="url(#tokenGrad)" dot={false} connectNulls />
              </AreaChart>
            </div>
          )}
        </div>
      </div>

      {/* Model Breakdown Donut */}
      <div className="overflow-hidden rounded-xl border border-border bg-white dark:bg-zinc-900 shadow-sm">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-orange-50/5 dark:bg-zinc-800/40">
          <div>
            <p className="text-xs font-bold text-foreground leading-tight">Model Usage Breakdown</p>
            <p className="text-[10px] text-muted-foreground mt-0.5">Distribution of LLM operations by model type</p>
          </div>
          <Cpu className="h-3.5 w-3.5 text-orange-600" />
        </div>
        <div className="p-4 flex items-center justify-center gap-4 h-48">
          {isLoadingMetrics ? (
            <div className="text-xs text-muted-foreground">Loading chart...</div>
          ) : modelUsageData.length > 0 ? (
            <>
              <PieChart width={150} height={150}>
                <Pie
                  data={modelUsageData}
                  dataKey="value"
                  nameKey="label"
                  innerRadius={28}
                  outerRadius={48}
                  paddingAngle={2}
                >
                  {modelUsageData.map((_, i) => (
                    <Cell key={i} fill={chartColors[i % chartColors.length]} />
                  ))}
                </Pie>
                <Tooltip content={<DonutTooltip />} />
              </PieChart>
              <div className="space-y-1.5 overflow-y-auto max-h-[140px] pr-2">
                {modelUsageData.map((slice, i) => (
                  <div key={slice.label} className="flex items-center gap-2 text-xxs leading-snug">
                    <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: chartColors[i % chartColors.length] }} />
                    <span className="text-muted-foreground truncate max-w-[120px]" title={slice.label}>{slice.label}</span>
                    <span className="font-semibold text-foreground">
                      {metrics?.by_model?.[i]?.total_cost > 0 ? `$${slice.value.toFixed(4)}` : slice.value}
                    </span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="text-xs text-muted-foreground italic">No model data recorded</div>
          )}
        </div>
      </div>

    </div>
  );

  const globalFilters = (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-end mb-4">
      <div className="flex flex-wrap items-center gap-2">
        {/* Trace Scope Filter */}
        {(isSuperAdmin || isLeaderExecutive || isDocApprover || isDepartmentAdmin) && (
          <select
            value={traceScopeFilter}
            onChange={(e) => {
              setTraceScopeFilter(e.target.value as any);
              setPage(1);
            }}
            className="h-8 rounded-lg border border-border bg-white dark:bg-zinc-900 px-3 text-xs font-semibold text-[#2D2926] dark:text-zinc-200 focus:outline-none focus:ring-1 focus:ring-[#D04A02] hover:bg-stone-50 dark:hover:bg-zinc-800 transition-colors"
          >
            {(isSuperAdmin || isLeaderExecutive || isDocApprover) && (
              <option value="all">Org Traces</option>
            )}
            <option value="dept">Dept Traces</option>
            <option value="my">My Traces</option>
          </select>
        )}

        {/* Department Selector (for privileged roles selecting Dept scope) */}
        {(isSuperAdmin || isLeaderExecutive || isDocApprover) && traceScopeFilter === "dept" && (
          <select
            value={selectedDeptId}
            onChange={(e) => {
              setSelectedDeptId(e.target.value);
              setPage(1);
            }}
            disabled={departments.length === 0}
            className="h-8 rounded-lg border border-border bg-white dark:bg-zinc-900 px-3 text-xs font-semibold text-[#2D2926] dark:text-zinc-200 focus:outline-none focus:ring-1 focus:ring-[#D04A02] hover:bg-stone-50 dark:hover:bg-zinc-800 transition-colors disabled:opacity-50"
          >
            {departments.length === 0 ? (
              <option value="">No Departments</option>
            ) : (
              departments.map((dept) => (
                <option key={dept.id} value={dept.id}>
                  {dept.name}
                </option>
              ))
            )}
          </select>
        )}

        {/* Environment Filter */}
        <select
          value={environmentFilter}
          onChange={(e) => {
            setEnvironmentFilter(e.target.value as any);
            setPage(1);
          }}
          className="h-8 rounded-lg border border-border bg-white dark:bg-zinc-900 px-3 text-xs font-semibold text-[#2D2926] dark:text-zinc-200 focus:outline-none focus:ring-1 focus:ring-[#D04A02] hover:bg-stone-50 dark:hover:bg-zinc-800 transition-colors"
        >
          <option value="all">All Environments</option>
          <option value="uat">UAT</option>
          <option value="production">Production</option>
        </select>

        {/* Date Range Filter */}
        <select
          value={dateRangeFilter}
          onChange={(e) => {
            setDateRangeFilter(e.target.value as any);
            setPage(1);
          }}
          className="h-8 rounded-lg border border-border bg-white dark:bg-zinc-900 px-3 text-xs font-semibold text-[#2D2926] dark:text-zinc-200 focus:outline-none focus:ring-1 focus:ring-[#D04A02] hover:bg-stone-50 dark:hover:bg-zinc-800 transition-colors"
        >
          <option value="1">Today</option>
          <option value="7">Last 7 Days</option>
          <option value="30">Last 30 Days</option>
        </select>
      </div>
    </div>
  );

  const tracesGrid = (
    <div className="rounded-xl border border-border bg-white dark:bg-zinc-900 shadow-sm overflow-hidden">
      
      {/* Table Filters Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between px-4 py-3 border-b border-border bg-stone-50/40 dark:bg-zinc-800/20">
        <p className="text-xs font-bold text-[#2D2926] dark:text-zinc-200">IDP Execution Trace Log Grid</p>
        
        <div className="flex flex-wrap items-center gap-2">
          {/* Search Input */}
          <form onSubmit={handleSearchSubmit} className="relative w-48 sm:w-56">
            <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-muted-foreground" />
            <input
              type="text"
              placeholder="Search traces..."
              value={searchVal}
              onChange={(e) => setSearchVal(e.target.value)}
              className="w-full h-7.5 rounded-lg border border-border bg-background pl-8 pr-3 text-xxs focus:outline-none focus:ring-1 focus:ring-[#D04A02] focus:border-[#D04A02]"
            />
          </form>
        </div>
      </div>

      {/* Data Table */}
      <div className="overflow-x-auto w-full">
        {isLoadingTraces ? (
          <div className="flex h-36 items-center justify-center text-xs text-muted-foreground">Loading trace logs...</div>
        ) : errorTraces ? (
          <div className="flex h-36 flex-col items-center justify-center p-4 text-center">
            <AlertTriangle className="h-6 w-6 text-red-500" />
            <p className="text-xs text-red-800 dark:text-red-400 mt-1 font-semibold">Could not load traces list</p>
            <p className="text-[10px] text-muted-foreground">{errorTraces}</p>
          </div>
        ) : tracesList.length === 0 ? (
          <div className="flex h-36 items-center justify-center text-xs text-muted-foreground italic">No pipeline runs recorded in this window.</div>
        ) : (
          <table className="min-w-full divide-y divide-border text-left">
            <thead className="bg-stone-50/40 dark:bg-zinc-800/10 text-xxs text-muted-foreground uppercase font-bold tracking-wider">
              <tr>
                <th className="px-4 py-2.5">Timestamp</th>
                <th className="px-4 py-2.5">Trace / Doc Name</th>
                <th className="px-4 py-2.5">Latency</th>
                <th className="px-4 py-2.5">Cost</th>
                <th className="px-4 py-2.5">Tokens</th>
                <th className="px-4 py-2.5">Models Used</th>
                <th className="px-4 py-2.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border text-xxs text-foreground leading-normal">
              {tracesList.map((trace) => (
                <tr key={trace.id} className="hover:bg-stone-50/50 dark:hover:bg-zinc-800/20 transition-colors">
                  <td className="px-4 py-3 whitespace-nowrap text-muted-foreground">
                    {trace.timestamp ? new Date(trace.timestamp).toLocaleString() : "N/A"}
                  </td>
                  <td className="px-4 py-3 font-semibold min-w-[140px] truncate max-w-xs" title={trace.name || trace.id}>
                    {trace.name || `Trace: ${trace.id.slice(0, 8)}...`}
                    {trace.level === "ERROR" && (
                      <span className="ml-1.5 inline-block rounded bg-red-100 dark:bg-red-950/30 px-1 py-0.5 text-[8px] font-bold text-red-700 dark:text-red-400 uppercase">
                        Err
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-muted-foreground font-mono">
                    {formatDuration(trace.latency_ms)}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-muted-foreground font-mono">
                    ${trace.total_cost.toFixed(5)}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-muted-foreground font-mono">
                    {trace.total_tokens.toLocaleString()}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1 max-w-[150px]">
                      {trace.models_used.map((model) => (
                        <span
                          key={model}
                          className="rounded bg-stone-100 dark:bg-zinc-800 px-1 py-0.5 text-[8px] font-mono text-foreground leading-none"
                        >
                          {model}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right whitespace-nowrap">
                    <div className="flex items-center justify-end gap-2">
                      {/* Deep-link to local Langfuse UI */}
                      <a
                        href={trace.langfuse_console_url || (tracesData?.langfuse_base_console_url ? `${tracesData.langfuse_base_console_url}/traces/${trace.id}` : `http://localhost:3001/project/default/traces/${trace.id}`)}
                        target="_blank"
                        rel="noreferrer"
                        className="p-1 rounded text-stone-500 hover:text-[#D04A02] hover:bg-stone-100 dark:hover:bg-zinc-800 transition-colors"
                        title="View on Langfuse Platform Console"
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                      
                      {/* Inline Timeline Drawer trigger */}
                      <button
                        type="button"
                        onClick={() => {
                          setSelectedTraceId(trace.id);
                          setIsDrawerOpen(true);
                        }}
                        className="flex items-center gap-1 px-2.5 py-1 rounded-md bg-[#D04A02]/10 hover:bg-[#D04A02]/25 text-[#D04A02] font-semibold transition-all active:scale-95"
                      >
                        <Terminal className="h-3 w-3" />
                        View Steps
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination Controls */}
      {!isLoadingTraces && !errorTraces && tracesList.length > 0 && (
        <div className="flex items-center justify-between border-t border-border px-4 py-3 bg-stone-50/20 dark:bg-zinc-800/10">
          <div className="text-xxs text-muted-foreground">
            Showing page <span className="font-semibold text-foreground">{page}</span> of{" "}
            <span className="font-semibold text-foreground">{totalPages}</span>
          </div>
          
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              disabled={page === 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="h-7 w-7 flex items-center justify-center rounded-lg border border-border bg-card text-muted-foreground hover:text-foreground disabled:opacity-50 transition-colors active:scale-90"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            
            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              className="h-7 w-7 flex items-center justify-center rounded-lg border border-border bg-card text-muted-foreground hover:text-foreground disabled:opacity-50 transition-colors active:scale-90"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

    </div>
  );

  if (viewMode === "tabs") {
    return (
      <div className="space-y-6">
        
        {/* Scope Warnings Alert block */}
        {metrics?.scope_warning && (
          <div className="flex items-start gap-2.5 rounded-xl border border-amber-200 bg-amber-50/50 dark:bg-amber-950/10 dark:border-amber-900/30 px-4 py-3 text-xs text-amber-800 dark:text-amber-400">
            <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-500 mt-0.5" />
            <div>
              <span className="font-bold">Observability Scope Warning:</span> {metrics.scope_warning_message}
            </div>
          </div>
        )}

        {globalFilters}

        <Tabs defaultValue="overview" className="w-full flex flex-col gap-6">
          <TabsList className="h-9 bg-transparent p-0 gap-1 border-b border-border w-full justify-start rounded-none">
            <TabsTrigger
              value="overview"
              className={cn(
                "h-9 rounded-none border-b-2 border-transparent px-4 text-sm font-medium text-muted-foreground transition-all",
                "data-[state=active]:border-[#D04A02] data-[state=active]:text-foreground data-[state=active]:shadow-none",
                "data-[state=active]:bg-transparent hover:text-foreground",
              )}
            >
              <Activity className="h-4 w-4 mr-2" />
              Overview
            </TabsTrigger>
            <TabsTrigger
              value="traces"
              className={cn(
                "h-9 rounded-none border-b-2 border-transparent px-4 text-sm font-medium text-muted-foreground transition-all",
                "data-[state=active]:border-[#D04A02] data-[state=active]:text-foreground data-[state=active]:shadow-none",
                "data-[state=active]:bg-transparent hover:text-foreground",
              )}
            >
              <Terminal className="h-4 w-4 mr-2" />
              Traces & Execution Logs
            </TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="space-y-6 outline-none focus:outline-none focus-visible:outline-none m-0">
            {kpisGrid}
            {chartsRow}
          </TabsContent>

          <TabsContent value="traces" className="outline-none focus:outline-none focus-visible:outline-none m-0">
            {tracesGrid}
          </TabsContent>
        </Tabs>

        {/* Timeline Drawer Subcomponent */}
        <ObservabilityTraceDrawer
          traceId={selectedTraceId}
          isOpen={isDrawerOpen}
          onClose={() => {
            setIsDrawerOpen(false);
            setSelectedTraceId(null);
          }}
        />

      </div>
    );
  }

  return (
    <div className="space-y-6">
      
      {/* Scope Warnings Alert block */}
      {metrics?.scope_warning && (
        <div className="flex items-start gap-2.5 rounded-xl border border-amber-200 bg-amber-50/50 dark:bg-amber-950/10 dark:border-amber-900/30 px-4 py-3 text-xs text-amber-800 dark:text-amber-400">
          <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-500 mt-0.5" />
          <div>
            <span className="font-bold">Observability Scope Warning:</span> {metrics.scope_warning_message}
          </div>
        </div>
      )}

      {globalFilters}
      {kpisGrid}
      {chartsRow}
      {tracesGrid}

      {/* 4. Timeline Drawer Subcomponent */}
      <ObservabilityTraceDrawer
        traceId={selectedTraceId}
        isOpen={isDrawerOpen}
        onClose={() => {
          setIsDrawerOpen(false);
          setSelectedTraceId(null);
        }}
      />

    </div>
  );
}

