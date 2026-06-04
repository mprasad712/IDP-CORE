import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { AgentCard } from "./components/AgentCard";
import { Button } from "@/components/ui/button";
import { Globe, Search } from "lucide-react";
import ActionModal from "./components/ActionModal";
import McpConfigModal from "./components/McpConfigModal";
import { useContext } from "react";
import { AuthContext } from "@/contexts/authContext";
import useAlertStore from "@/stores/alertStore";
import { useCustomNavigate } from "@/customization/hooks/use-custom-navigate";
import { useDeployPackageRequest, useGetPackageRequestsForApproval } from "@/controllers/API/queries/packages";
import useRegionStore from "@/stores/regionStore";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { useGetApprovals, type ApprovalAgent } from "@/controllers/API/queries/approvals";
import { useApprovalActionModal, useApprovalActions } from "./hooks";
import CustomLoader from "@/customization/components/custom-loader";

type FilterType = "all" | "pending" | "approved" | "rejected" | "deployed" | "cancelled";
type ApprovalTabType = "agent" | "model" | "mcp" | "package";

const APPROVAL_TABS: Array<{ id: ApprovalTabType; label: string; permission: string }> = [
  { id: "agent", label: "AI Agent", permission: "view_agent" },
  { id: "model", label: "Model", permission: "view_model" },
  { id: "mcp", label: "MCP", permission: "view_mcp" },
  { id: "package", label: "Package", permission: "view_packages_page" },
];

export default function ApprovalPage() {
  const { t } = useTranslation();
  /* ================= STATE ================= */
  const [filter, setFilter] = useState<FilterType>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [activeTab, setActiveTab] = useState<ApprovalTabType>("agent");
  const navigate = useCustomNavigate();
  const { permissions, role } = useContext(AuthContext);
  const setErrorData = useAlertStore((state) => state.setErrorData);
  const setSuccessData = useAlertStore((state) => state.setSuccessData);
  const can = (permissionKey: string) => permissions?.includes(permissionKey);
  const isRoot = String(role ?? "").toLowerCase() === "root";
  const regions = useRegionStore((s) => s.regions);
  const selectedRegionCode = useRegionStore((s) => s.selectedRegionCode);
  const setSelectedRegion = useRegionStore((s) => s.setSelectedRegion);
  const fetchRegions = useRegionStore((s) => s.fetchRegions);
  const packageRegionCode = isRoot ? selectedRegionCode : null;
  const [isMcpConfigOpen, setIsMcpConfigOpen] = useState(false);
  const [selectedMcpApprovalId, setSelectedMcpApprovalId] = useState<string | null>(null);

  /* ================= MODAL & ACTIONS MANAGEMENT ================= */
  const { isOpen, selectedAgent, action, openModal, closeModal } =
    useApprovalActionModal();
  const { handleApprove, handleReject } = useApprovalActions(packageRegionCode);

  /* ================= API QUERIES ================= */
  // Fetch all approvals from backend
  const { data: agents = [], isLoading: isLoadingAgents } = useGetApprovals();
  const { data: packageRequests = [], isLoading: isLoadingPackageRequests } =
    useGetPackageRequestsForApproval(
      { regionCode: packageRegionCode },
      {
        enabled: isRoot,
      },
    );
  const deployPackageRequestMutation = useDeployPackageRequest();

  useEffect(() => {
    if (isRoot && regions.length === 0) {
      fetchRegions();
    }
  }, [isRoot, regions.length, fetchRegions]);

  const isRemoteRegion =
    isRoot && !!selectedRegionCode && regions.length > 0
      ? (() => {
          const hub = regions.find((region) => region.is_hub);
          return hub ? hub.code !== selectedRegionCode : false;
        })()
      : false;

  const visibleTabs = isRoot
    ? APPROVAL_TABS.filter((tab) => tab.id === "package")
    : APPROVAL_TABS.filter((tab) => tab.id !== "package" && can(tab.permission));

  useEffect(() => {
    if (visibleTabs.length === 0) return;
    if (!visibleTabs.some((tab) => tab.id === activeTab)) {
      setActiveTab(visibleTabs[0].id);
    }
  }, [activeTab, visibleTabs]);

  useEffect(() => {
    if (activeTab !== "package" && (filter === "deployed" || filter === "cancelled")) {
      setFilter("all");
    }
  }, [activeTab, filter]);

  /* ================= FILTERING & CALCULATIONS ================= */
  const packageApprovalCards: ApprovalAgent[] = packageRequests.map((request) => ({
    id: request.id,
    entityType: "package",
    title: `${request.package_name}`,
    status: request.status,
    description: request.justification,
    submittedBy: {
      name:
        request.requested_by_name ||
        request.requested_by_email ||
        request.requested_by,
      email: request.requested_by_email ?? null,
    },
    project: request.service_name,
    submitted: request.requested_at,
    version: request.requested_version,
    recentChanges: request.review_comments || request.deployment_notes || "-",
  }));

  const sourceApprovals = activeTab === "package" ? packageApprovalCards : agents;

  const filteredAgents = sourceApprovals.filter((agent) => {
    const entityType = (agent.entityType || "agent") as ApprovalTabType;
    const matchesTab = entityType === activeTab;
    const matchesFilter = filter === "all" ? true : agent.status === filter;
    const matchesSearch =
      searchQuery === "" ||
      agent.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      agent.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (agent.project || "").toLowerCase().includes(searchQuery.toLowerCase()) ||
      (agent.visibility || "").toLowerCase().includes(searchQuery.toLowerCase());
    return matchesTab && matchesFilter && matchesSearch;
  });

  const pendingCount = sourceApprovals.filter((a) => a.status === "pending").length;
  const noAgentsMessage =
    filter === "pending"
      ? t("No pending agents found")
      : filter === "approved"
        ? t("No approved agents found")
        : filter === "rejected"
          ? t("No rejected agents found")
          : filter === "deployed"
            ? t("No deployed requests found")
            : filter === "cancelled"
              ? t("No cancelled requests found")
              : t("No agents found");

  /* ================= EVENT HANDLERS ================= */
  const handleApproveClick = (agent: ApprovalAgent) => {
    openModal(agent, "approve");
  };

  const handleRejectClick = (agent: ApprovalAgent) => {
    openModal(agent, "reject");
  };

  const handleMcpConfigClick = (agent: ApprovalAgent) => {
    if ((agent.entityType || "agent") !== "mcp") return;
    setSelectedMcpApprovalId(agent.id);
    setIsMcpConfigOpen(true);
  };

  const handlePackageDeploy = async (agent: ApprovalAgent) => {
    await new Promise((resolve, reject) => {
      deployPackageRequestMutation.mutate(
        {
          requestId: agent.id,
          deployment_notes: "Marked as deployed by root",
          regionCode: packageRegionCode,
        },
        {
          onSuccess: () => {
            setSuccessData({ title: `Package "${agent.title}" marked as deployed.` });
            resolve(null);
          },
          onError: () => {
            setErrorData({ title: `Failed to mark package "${agent.title}" as deployed.` });
            reject(new Error("Package deploy action failed"));
          },
        },
      );
    });
  };

  /**
   * Handle the final action submission from the modal
   * Calls either handleApprove or handleReject based on the action type
   */
  const handleSubmitAction = async (data: {
    comments: string;
    attachments: File[];
  }) => {
    if (!selectedAgent) return;

    if (action === "approve") {
      await handleApprove(selectedAgent, data.comments, data.attachments);
    } else {
      await handleReject(selectedAgent, data.comments, data.attachments);
    }

  };

  /* ── Status colour map ── */
  const statusColors: Record<string, string> = {
    pending:   "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
    approved:  "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
    rejected:  "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
    deployed:  "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
    cancelled: "bg-muted text-muted-foreground",
  };

  const approvedCount  = sourceApprovals.filter((a) => a.status === "approved").length;
  const rejectedCount  = sourceApprovals.filter((a) => a.status === "rejected").length;
  const deployedCount  = sourceApprovals.filter((a) => a.status === "deployed").length;

  const filterOptions = activeTab === "package"
    ? (["all", "pending", "approved", "rejected", "deployed", "cancelled"] as FilterType[])
    : (["all", "pending", "approved", "rejected"] as FilterType[]);

  const filterCount = (f: FilterType) =>
    f === "all" ? sourceApprovals.length : sourceApprovals.filter((a) => a.status === f).length;

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-[#F7F5F3] dark:bg-[hsl(222,28%,8%)]">

      {/* ══ PAGE HEADER ══ */}
      <div className="flex-shrink-0 border-b bg-background shadow-sm">
        <div className="px-6 py-5">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">

            {/* Title + description */}
            <div className="flex items-center gap-3">
              <div
                className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-xl shadow-sm"
                style={{ background: "linear-gradient(135deg,rgba(208,74,2,0.15) 0%,rgba(208,74,2,0.06) 100%)" }}
              >
                <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="#D04A02" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                  <path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/>
                </svg>
              </div>
              <div>
                <h1 className="text-xl font-bold text-foreground">{t("Review & Approval")}</h1>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {t("Manage agent, model, MCP, and package requests")}
                </p>
              </div>
            </div>

            {/* Region + Search */}
            <div className="flex flex-wrap items-center gap-2">
              {isRoot && regions.length > 0 && (
                <div className="flex items-center gap-1.5 rounded-lg border border-border bg-muted/40 px-2 py-1.5">
                  <Globe className="h-3.5 w-3.5 text-muted-foreground" />
                  <Select value={selectedRegionCode ?? ""} onValueChange={setSelectedRegion}>
                    <SelectTrigger className="h-auto border-none bg-transparent p-0 text-xs font-medium shadow-none focus:ring-0 w-[160px]">
                      <SelectValue placeholder={t("Select region")} />
                    </SelectTrigger>
                    <SelectContent>
                      {regions.filter((r) => r.code).map((r) => (
                        <SelectItem key={r.code} value={r.code}>
                          {r.name}{r.is_hub ? ` (${t("Hub")})` : ""}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
              <div className="relative">
                <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <input
                  type="text"
                  placeholder={t("Search…")}
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="h-9 rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-sm text-foreground placeholder:text-muted-foreground focus:border-[#D04A02] focus:outline-none focus:ring-2 focus:ring-[#D04A02]/10 sm:w-56"
                />
              </div>
            </div>
          </div>

          {/* ── Stats strip ── */}
          <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              { label: t("Total"),    value: sourceApprovals.length, accent: "text-foreground",       bg: "bg-muted/50" },
              { label: t("Pending"),  value: pendingCount,           accent: "text-amber-600 dark:text-amber-400",   bg: "bg-amber-50 dark:bg-amber-900/20" },
              { label: t("Approved"), value: approvedCount,          accent: "text-emerald-600 dark:text-emerald-400", bg: "bg-emerald-50 dark:bg-emerald-900/20" },
              { label: t("Rejected"), value: rejectedCount,          accent: "text-red-600 dark:text-red-400",       bg: "bg-red-50 dark:bg-red-900/20" },
            ].map((s) => (
              <div
                key={s.label}
                className={`flex items-center gap-3 rounded-xl border border-border/50 px-4 py-3 ${s.bg}`}
              >
                <span className={`text-2xl font-bold tabular-nums ${s.accent}`}>{s.value}</span>
                <span className="text-xs font-medium text-muted-foreground">{s.label}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Remote region notice */}
        {isRoot && isRemoteRegion && selectedRegionCode && (
          <div className="border-t border-amber-200 bg-amber-50/70 px-6 py-2.5 dark:border-amber-900/30 dark:bg-amber-950/10">
            <p className="text-xs text-amber-800 dark:text-amber-200">
              {t("Viewing package approvals for {{region}} from hub.", {
                region: regions.find((r) => r.code === selectedRegionCode)?.name ?? selectedRegionCode,
              })}
            </p>
          </div>
        )}

        {/* ── Type tabs + Status filter pills ── */}
        <div className="flex flex-wrap items-center gap-3 border-t border-border/50 px-6 py-3">

          {/* Segmented type selector */}
          {visibleTabs.length > 1 && (
            <div className="flex items-center rounded-lg border border-border bg-muted/50 p-0.5 gap-0.5">
              {visibleTabs.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setActiveTab(tab.id)}
                  className={[
                    "rounded-md px-3 py-1.5 text-xs font-semibold transition-all duration-150",
                    activeTab === tab.id
                      ? "bg-background shadow-sm text-[#D04A02]"
                      : "text-muted-foreground hover:text-foreground",
                  ].join(" ")}
                >
                  {t(tab.label)}
                </button>
              ))}
            </div>
          )}

          <div className="h-5 w-px bg-border/60" />

          {/* Status filter pills */}
          <div className="flex flex-wrap gap-1.5">
            {filterOptions.map((type) => {
              const count = filterCount(type);
              const active = filter === type;
              return (
                <button
                  key={type}
                  type="button"
                  onClick={() => setFilter(type)}
                  className={[
                    "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold transition-all duration-150",
                    active
                      ? "border-[#D04A02] bg-[#D04A02] text-white shadow-sm"
                      : "border-border text-muted-foreground hover:border-[#D04A02]/50 hover:text-[#D04A02]",
                  ].join(" ")}
                >
                  {t(type.charAt(0).toUpperCase() + type.slice(1))}
                  {count > 0 && (
                    <span className={`rounded-full px-1.5 text-[9px] font-bold ${active ? "bg-white/20" : "bg-muted"}`}>
                      {count}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* ══ CONTENT ══ */}
      <div className="flex-1 overflow-auto p-6">
        {isLoadingAgents || (activeTab === "package" && isLoadingPackageRequests) ? (
          <div className="flex h-48 items-center justify-center">
            <CustomLoader />
          </div>
        ) : filteredAgents.length === 0 ? (
          /* ── Empty state ── */
          <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-background py-20 text-center">
            <div
              className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl"
              style={{ background: "rgba(208,74,2,0.08)" }}
            >
              <svg className="h-8 w-8 text-[#D04A02]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/>
              </svg>
            </div>
            <h3 className="text-base font-semibold text-foreground">
              {searchQuery ? t('No results for “{{q}}”', { q: searchQuery }) : t("Nothing to review")}
            </h3>
            <p className="mt-1 max-w-xs text-sm text-muted-foreground">
              {searchQuery ? t("Try a different search term or clear the filter.") : noAgentsMessage}
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {filteredAgents.map((agent) => (
              <AgentCard
                key={agent.id}
                {...agent}
                entityType={agent.entityType}
                onReject={() => handleRejectClick(agent)}
                onApprove={() => handleApproveClick(agent)}
                onReviewDetails={() =>
                  agent.entityType === "mcp"
                    ? setErrorData({ title: t("Use MCP Config for MCP approvals") })
                    : agent.entityType === "package"
                      ? undefined
                      : navigate(`/approval/${agent.id}/review`)
                }
                onViewMcpConfig={() => handleMcpConfigClick(agent)}
                onDeploy={() => handlePackageDeploy(agent)}
              />
            ))}
          </div>
        )}
      </div>

      <ActionModal
        open={isOpen}
        setOpen={closeModal}
        action={action}
        entityType={selectedAgent?.entityType}
        agentTitle={selectedAgent?.title || ""}
        onSubmit={handleSubmitAction}
      />
      <McpConfigModal
        open={isMcpConfigOpen}
        setOpen={setIsMcpConfigOpen}
        approvalId={selectedMcpApprovalId}
      />
    </div>
  );
}
