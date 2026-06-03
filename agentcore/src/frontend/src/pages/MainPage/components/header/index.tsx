import { debounce } from "lodash";
import { useCallback, useEffect, useState } from "react";
import ForwardedIconComponent from "@/components/common/genericIconComponent";
import SemanticSearchToggle from "@/components/common/semanticSearchToggle";
import ShadTooltip from "@/components/common/shadTooltipComponent";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { useDeleteDeleteAgents } from "@/controllers/API/queries/agents/use-delete-delete-agents";
import { useGetDownloadAgents } from "@/controllers/API/queries/agents/use-get-download-agents";
import DeleteConfirmationModal from "@/modals/deleteConfirmationModal";
import useAlertStore from "@/stores/alertStore";
import { cn } from "@/utils/utils";
import { useContext } from "react";
import { AuthContext } from "@/contexts/authContext";

interface HeaderComponentProps {
  agentType: "agents" | "components" | "mcp";
  setAgentType: (agentType: "agents" | "components" | "mcp") => void;
  view: "list" | "grid";
  setView: (view: "list" | "grid") => void;
  setNewProjectModal: (newProjectModal: boolean) => void;
  folderName?: string;
  setSearch: (search: string) => void;
  isEmptyFolder: boolean;
  selectedAgents: string[];
  allowCreateInProject: boolean;
  semanticEnabled?: boolean;
  onSemanticToggle?: (enabled: boolean) => void;
  isSemanticSearching?: boolean;
}

const HeaderComponent = ({
  folderName = "",
  agentType,
  setAgentType,
  view,
  setView,
  setNewProjectModal,
  setSearch,
  isEmptyFolder,
  selectedAgents,
  allowCreateInProject,
  semanticEnabled = false,
  onSemanticToggle,
  isSemanticSearching = false,
}: HeaderComponentProps) => {
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const setSuccessData = useAlertStore((state) => state.setSuccessData);
  // Debounce the setSearch function from the parent
  const debouncedSetSearch = useCallback(
    debounce((value: string) => {
      setSearch(value);
    }, 1000),
    [setSearch],
  );

  const { mutate: downloadAgents, isPending: isDownloading } =
    useGetDownloadAgents();
  const { mutate: deleteAgents, isPending: isDeleting } = useDeleteDeleteAgents();

  const { permissions } = useContext(AuthContext);
  const can = (permissionKey: string) => permissions?.includes(permissionKey);
  const canCreateAgent =
    can("edit_agents") ||
    can("view_projects_page") ||
    can("view_project_page");
  const canCreateAgentInProject = canCreateAgent && allowCreateInProject;

  useEffect(() => {
    debouncedSetSearch(debouncedSearch);

    return () => {
      debouncedSetSearch.cancel(); // Cleanup on unmount
    };
  }, [debouncedSearch, debouncedSetSearch]);

  const handleSearch = (e: React.ChangeEvent<HTMLInputElement>) => {
    setDebouncedSearch(e.target.value);
  };

  const tabTypes = ["components", "agents"];

  const handleDownload = () => {
    downloadAgents({ ids: selectedAgents });
    setSuccessData({ title: "Agents downloaded successfully" });
  };

  const handleDelete = () => {
    deleteAgents(
      { agent_ids: selectedAgents },
      {
        onSuccess: () => {
          setSuccessData({ title: "Agents deleted successfully" });
        },
      },
    );
  };

  return (
    <>
      {/* ── Page title row ── */}
      <div className="flex items-center justify-between pb-5" data-testid="mainpage_title">
        <div className="flex items-center gap-3">
          {/* Mobile sidebar trigger */}
          <div className="h-7 w-8 flex-shrink-0 transition-all group-data-[open=true]/sidebar-wrapper:md:w-0 lg:hidden">
            <div className="opacity-100 transition-all group-data-[open=true]/sidebar-wrapper:md:opacity-0">
              <SidebarTrigger>
                <ForwardedIconComponent name="PanelLeftOpen" aria-hidden="true" className="" />
              </SidebarTrigger>
            </div>
          </div>

          {/* Folder icon + name */}
          {folderName && (
            <div className="flex items-center gap-2.5">
              <div
                className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg"
                style={{ background: "rgba(208,74,2,0.10)" }}
              >
                <ForwardedIconComponent
                  name="FolderOpen"
                  className="h-4 w-4"
                  style={{ color: "#D04A02" } as React.CSSProperties}
                />
              </div>
              <div>
                <h1 className="text-base font-bold leading-tight text-foreground">{folderName}</h1>
                <p className="text-[11px] text-muted-foreground capitalize">
                  {agentType === "mcp" ? "MCP Servers" : agentType}
                </p>
              </div>
            </div>
          )}
        </div>

        {/* New agent button — top-right */}
        {canCreateAgentInProject && !isEmptyFolder && agentType !== "mcp" && (
          <Button
            variant="default"
            size="md"
            className="gap-1.5 px-3"
            onClick={() => setNewProjectModal(true)}
            id="new-project-btn"
            data-testid="new-project-btn"
          >
            <ForwardedIconComponent name="Plus" aria-hidden="true" className="h-3.5 w-3.5" />
            <span className="hidden font-semibold sm:inline">New Agent</span>
          </Button>
        )}
      </div>

      {!isEmptyFolder && agentType !== "mcp" && (
        <>
          {/* ── Toolbar row: search + view toggle + bulk actions ── */}
          <div className="mb-4 flex items-center gap-2">
            {/* Search */}
            <div className="relative flex-1 xl:max-w-sm">
              <Input
                icon="Search"
                data-testid="search-store-input"
                type="text"
                placeholder={semanticEnabled ? `Semantic search…` : `Search ${agentType}…`}
                className="w-full !text-mmd"
                inputClassName="!text-mmd"
                value={debouncedSearch}
                onChange={handleSearch}
              />
            </div>

            {/* Semantic toggle */}
            {onSemanticToggle && (
              <SemanticSearchToggle
                enabled={semanticEnabled}
                onToggle={onSemanticToggle}
                isSearching={isSemanticSearching}
              />
            )}

            {/* View toggle pill */}
            <div className="relative flex h-9 flex-shrink-0 items-center rounded-lg border border-border bg-muted p-0.5">
              <div
                className={cn(
                  "absolute h-[28px] w-8 rounded-md bg-background shadow-sm transition-transform duration-200",
                  view === "list" ? "left-[2px] translate-x-0" : "left-[2px] translate-x-full",
                )}
              />
              {(["list", "grid"] as const).map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setView(v)}
                  className={cn(
                    "relative z-10 flex h-7 w-8 items-center justify-center rounded-md transition-colors duration-150",
                    view === v ? "text-foreground" : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <ForwardedIconComponent
                    name={v === "list" ? "Menu" : "LayoutGrid"}
                    aria-hidden="true"
                    className="h-3.5 w-3.5"
                  />
                </button>
              ))}
            </div>

            {/* Bulk actions (slide in when items selected) */}
            <div
              className={cn(
                "flex w-0 items-center gap-1.5 overflow-hidden opacity-0 transition-all duration-300",
                selectedAgents.length > 0 && "w-28 opacity-100",
              )}
            >
              <Button variant="outline" size="iconMd" className="h-8 w-8 flex-shrink-0" data-testid="download-bulk-btn" onClick={handleDownload} loading={isDownloading}>
                <ForwardedIconComponent name="Download" />
              </Button>
              <DeleteConfirmationModal
                onConfirm={handleDelete}
                description={"agent" + (selectedAgents.length > 1 ? "s" : "")}
                note={"and " + (selectedAgents.length > 1 ? "their" : "its") + " message history"}
              >
                <Button variant="destructive" size="iconMd" className="h-8 flex-shrink-0 gap-1 px-2 !text-mmd" data-testid="delete-bulk-btn" loading={isDeleting}>
                  <ForwardedIconComponent name="Trash2" className="h-3.5 w-3.5" />
                  <span className="text-xs font-medium">Delete</span>
                </Button>
              </DeleteConfirmationModal>
            </div>
          </div>
        </>
      )}
    </>
  );
};

export default HeaderComponent;
