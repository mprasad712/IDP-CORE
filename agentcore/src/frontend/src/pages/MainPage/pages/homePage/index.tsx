import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import PaginatorComponent from "@/components/common/paginatorComponent";
import CardsWrapComponent from "@/components/core/cardsWrapComponent";
import { IS_MAC } from "@/constants/constants";
import { useGetFolderQuery } from "@/controllers/API/queries/folders/use-get-folder";
import { useSemanticSearch } from "@/controllers/API/queries/semantic-search/use-semantic-search";
import { CustomBanner } from "@/customization/components/custom-banner";
import {
  ENABLE_AGENTCORE,
} from "@/customization/feature-flags";
import { useCustomNavigate } from "@/customization/hooks/use-custom-navigate";
import { useFolderStore } from "@/stores/foldersStore";
import HeaderComponent from "../../components/header";
import ListComponent from "../../components/list";
import ListSkeleton from "../../components/listSkeleton";
import ModalsComponent from "../../components/modalsComponent";
import useFileDrop from "../../hooks/use-on-file-drop";
import EmptyFolder from "../emptyFolder";
import { useContext } from "react";
import { AuthContext } from "@/contexts/authContext";

const HomePage = ({ type }: { type: "agents" | "components" | "mcp" }) => {
  const [view, setView] = useState<"grid" | "list">(() => {
    const savedView = localStorage.getItem("view");
    return savedView === "grid" || savedView === "list" ? savedView : "list";
  });
  const [newProjectModal, setNewProjectModal] = useState(false);
  const { folderId } = useParams();
  const [pageIndex, setPageIndex] = useState(1);
  const [pageSize, setPageSize] = useState(12);
  const [search, setSearch] = useState("");
  const [semanticEnabled, setSemanticEnabled] = useState(false);
  const navigate = useCustomNavigate();

  const { permissions, role } = useContext(AuthContext);
  const can = (permissionKey: string) => permissions?.includes(permissionKey);

  const [agentType, setAgentType] = useState<"agents" | "components" | "mcp">(
    type,
  );
  const myCollectionId = useFolderStore((state) => state.myCollectionId);
  const folders = useFolderStore((state) => state.folders);
  const folderName =
    folders.find((folder) => folder.id === folderId)?.name ??
    folders[0]?.name ??
    "";
  const activeFolderId = folderId ?? myCollectionId;
  const activeFolder = folders.find((folder) => folder.id === activeFolderId);
  const allowCreateInProject = Boolean(activeFolder?.is_own_project);

  useEffect(() => {
    // Only check if we have a folderId and folders have loaded
    if (folderId && folders && folders.length > 0) {
      const folderExists = folders.find((folder) => folder.id === folderId);
      if (!folderExists) {
        // Folder doesn't exist for this user, redirect to /all
        console.error("Invalid folderId, redirecting to /all");
        navigate("/all");
      }
    }
  }, [folderId, folders, navigate]);

  // When semantic search is active, fetch all agents in folder (no pagination limit)
  // so we can properly filter against Pinecone results
  const isSemanticActive = semanticEnabled && !!search;

  const { data: folderData, isLoading } = useGetFolderQuery({
    id: folderId ?? myCollectionId!,
    page: isSemanticActive ? 1 : pageIndex,
    size: isSemanticActive ? 100 : pageSize,
    is_component: agentType === "components",
    is_agent: agentType === "agents",
    search: isSemanticActive ? "" : search,
  });

  const { data: semanticData, isLoading: isSemanticLoading } = useSemanticSearch(
    isSemanticActive
      ? { entity_type: "agents", q: search, top_k: 50 }
      : null,
    { enabled: isSemanticActive },
  );

  // When semantic search is active, filter folder agents to only show matched IDs
  const semanticIds = useMemo(() => {
    if (!isSemanticActive || !semanticData?.results) return null;
    return new Set(semanticData.results.map((r) => r.id));
  }, [isSemanticActive, semanticData]);

  const allAgents = folderData?.agents?.items ?? [];
  const displayAgents = useMemo(() => {
    if (!semanticIds) return allAgents;
    // Filter to agents that exist in BOTH the folder AND semantic results
    // Preserve semantic ranking order
    const agentMap = new Map(allAgents.map((a) => [a.id, a]));
    const ranked: typeof allAgents = [];
    for (const result of semanticData?.results ?? []) {
      const agent = agentMap.get(result.id);
      if (agent) ranked.push(agent);
    }
    return ranked;
  }, [allAgents, semanticIds, semanticData]);

  const data = {
    agents: displayAgents,
    name: folderData?.project?.name ?? "",
    description: folderData?.project?.description ?? "",
    parent_id: folderData?.project?.parent_id ?? "",
    components: folderData?.project?.components ?? [],
    pagination: isSemanticActive
      ? { page: 1, size: displayAgents.length, total: displayAgents.length, pages: 1 }
      : {
          page: folderData?.agents?.page ?? 1,
          size: folderData?.agents?.size ?? 12,
          total: folderData?.agents?.total ?? 0,
          pages: folderData?.agents?.pages ?? 0,
        },
  };

  useEffect(() => {
    localStorage.setItem("view", view);
  }, [view]);

  const handlePageChange = useCallback((newPageIndex, newPageSize) => {
    setPageIndex(newPageIndex);
    setPageSize(newPageSize);
  }, []);

  const onSearch = useCallback((newSearch) => {
    setSearch(newSearch);
    setPageIndex(1);
  }, []);

  // Use the paginated folder data (from GET /projects/{id}) to determine
  // if the folder is empty, rather than the global agents store which may
  // not have loaded yet or may be stale.
  const isEmptyFolder =
    !isLoading &&
    (folderData?.agents?.total ?? 0) === 0 &&
    !search;

  const handleFileDrop = useFileDrop(isEmptyFolder ? undefined : agentType);

  useEffect(() => {
    // If this tab has no items but the other tab does, auto-switch.
    // Use paginated data instead of global store for accuracy.
    if (
      !isEmptyFolder &&
      data.agents.length === 0 &&
      !isLoading
    ) {
      // Current tab is empty, check if we should switch
      setAgentType(agentType === "agents" ? "components" : "agents");
    }
  }, [isEmptyFolder, data.agents.length, isLoading]);

  const [selectedAgents, setSelectedAgents] = useState<string[]>([]);
  const [lastSelectedIndex, setLastSelectedIndex] = useState<number | null>(
    null,
  );
  const [isShiftPressed, setIsShiftPressed] = useState(false);
  const [isCtrlPressed, setIsCtrlPressed] = useState(false);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Only track these keys when we're in list/selection mode and not when a modal is open
      // or when an input field is focused
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        (e.target instanceof HTMLElement && e.target.isContentEditable)
      ) {
        return;
      }

      if (e.key === "Shift") {
        setIsShiftPressed(true);
      } else if ((!IS_MAC && e.key === "Control") || e.key === "Meta") {
        setIsCtrlPressed(true);
      }
    };

    const handleKeyUp = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        (e.target instanceof HTMLElement && e.target.isContentEditable)
      ) {
        return;
      }

      if (e.key === "Shift") {
        setIsShiftPressed(false);
      } else if ((!IS_MAC && e.key === "Control") || e.key === "Meta") {
        setIsCtrlPressed(false);
      }
    };

    // Reset key states when window loses focus
    const handleBlur = () => {
      setIsShiftPressed(false);
      setIsCtrlPressed(false);
    };

    // Only add listeners if we're in agents or components mode, not MCP mode
    if (agentType === "agents" || agentType === "components") {
      document.addEventListener("keydown", handleKeyDown);
      document.addEventListener("keyup", handleKeyUp);
      window.addEventListener("blur", handleBlur);
    }

    // Clean up event listeners when component unmounts
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("keyup", handleKeyUp);
      window.removeEventListener("blur", handleBlur);

      // Reset key states on unmount
      setIsShiftPressed(false);
      setIsCtrlPressed(false);
    };
  }, [agentType]);

  const setSelectedAgent = useCallback(
    (selected: boolean, agentId: string, index: number) => {
      setLastSelectedIndex(index);
      if (isShiftPressed && lastSelectedIndex !== null) {
        // Find the indices of the last selected and current agent
        const agents = data.agents;

        // Determine the range to select
        const start = Math.min(lastSelectedIndex, index);
        const end = Math.max(lastSelectedIndex, index);
        // Get all agent IDs in the range
        const agentsToSelect = agents
          .slice(start, end + 1)
          .map((agent) => agent.id);

        // Update selection
        if (selected) {
          setSelectedAgents((prev) =>
            Array.from(new Set([...prev, ...agentsToSelect])),
          );
        } else {
          setSelectedAgents((prev) =>
            prev.filter((id) => !agentsToSelect.includes(id)),
          );
        }
      } else {
        if (selected) {
          setSelectedAgents([...selectedAgents, agentId]);
        } else {
          setSelectedAgents(selectedAgents.filter((id) => id !== agentId));
        }
      }
    },
    [selectedAgents, lastSelectedIndex, data.agents, isShiftPressed],
  );

  useEffect(() => {
    setSelectedAgents((old) =>
      old.filter((id) => data.agents.some((agent) => agent.id === id)),
    );
  }, [folderData?.agents?.items]);

  // Reset key states when navigating away
  useEffect(() => {
    return () => {
      setIsShiftPressed(false);
      setIsCtrlPressed(false);
    };
  }, [folderId]);

  return (
    <CardsWrapComponent
      onFileDrop={handleFileDrop}
      dragMessage={`Drop your ${isEmptyFolder ? "agents or components" : agentType} here`}
    >
      <div
        className="flex h-full w-full flex-col overflow-y-auto"
        data-testid="cards-wrapper"
      >
        <div className="flex h-full w-full flex-col 3xl:container">
          {ENABLE_AGENTCORE && <CustomBanner />}

          {/* ── Toolbar / page header ── */}
          <div className="flex-shrink-0 border-b bg-background px-5 py-4 shadow-sm">
            <HeaderComponent
              folderName={folderName}
              agentType={agentType}
              setAgentType={setAgentType}
              view={view}
              setView={setView}
              setNewProjectModal={setNewProjectModal}
              setSearch={onSearch}
              isEmptyFolder={isEmptyFolder}
              selectedAgents={selectedAgents}
              allowCreateInProject={allowCreateInProject}
              semanticEnabled={semanticEnabled}
              onSemanticToggle={setSemanticEnabled}
              isSemanticSearching={isSemanticLoading && isSemanticActive}
            />
          </div>

          {/* ── Content area ── */}
          <div className="flex flex-1 flex-col overflow-y-auto p-5">
            {isEmptyFolder ? (
              <EmptyFolder
                setOpenModal={setNewProjectModal}
                allowCreateInProject={allowCreateInProject}
              />
            ) : (
              <div className="flex h-full flex-col">
                {isLoading ? (
                  view === "grid" ? (
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
                      <ListSkeleton /><ListSkeleton /><ListSkeleton />
                    </div>
                  ) : (
                    <div className="flex flex-col gap-2">
                      <ListSkeleton /><ListSkeleton /><ListSkeleton />
                    </div>
                  )
                ) : (agentType === "agents" || agentType === "components") && data && data.pagination.total > 0 ? (
                  view === "grid" ? (
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
                      {data.agents.map((agent, index) => (
                        <ListComponent
                          key={agent.id}
                          agentData={agent}
                          index={index}
                          selected={selectedAgents.includes(agent.id)}
                          setSelected={(selected) => setSelectedAgent(selected, agent.id, index)}
                          shiftPressed={isShiftPressed || isCtrlPressed}
                        />
                      ))}
                    </div>
                  ) : (
                    <div className="flex flex-col gap-1.5">
                      {data.agents.map((agent, index) => (
                        <ListComponent
                          key={agent.id}
                          agentData={agent}
                          index={index}
                          selected={selectedAgents.includes(agent.id)}
                          setSelected={(selected) => setSelectedAgent(selected, agent.id, index)}
                          shiftPressed={isShiftPressed || isCtrlPressed}
                          disabled={can("view_agents_page") && !can("edit_agents")}
                        />
                      ))}
                    </div>
                  )
                ) : (
                  /* ── Inline empty state when no results match filter/search ── */
                  <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-background py-16 text-center">
                    <div
                      className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl"
                      style={{ background: "rgba(208,74,2,0.08)" }}
                    >
                      <svg className="h-6 w-6 text-[#D04A02]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
                        <circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>
                      </svg>
                    </div>
                    <p className="text-sm font-semibold text-foreground">
                      {agentType === "agents" ? "No agents found" : "No components found"}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {agentType === "agents"
                        ? "Try a different search, or create a new agent."
                        : "No saved or custom components in this project."}
                    </p>
                    {agentType === "agents" && allowCreateInProject && (
                      <button
                        onClick={() => setNewProjectModal(true)}
                        className="mt-4 rounded-lg border border-[#D04A02]/30 bg-[rgba(208,74,2,0.06)] px-4 py-1.5 text-xs font-semibold text-[#D04A02] transition-colors hover:bg-[rgba(208,74,2,0.12)]"
                      >
                        Create agent
                      </button>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* ── Pagination ── */}
          {(agentType === "agents" || agentType === "components") &&
            !isLoading && !isEmptyFolder && data.pagination.total >= 10 && (
              <div className="flex flex-shrink-0 items-center justify-between border-t bg-background px-5 py-3">
                <p className="text-xs text-muted-foreground">
                  {data.pagination.total} {agentType} total
                </p>
                <PaginatorComponent
                  pageIndex={data.pagination.page}
                  pageSize={data.pagination.size}
                  rowsCount={[12, 24, 48, 96]}
                  totalRowsCount={data.pagination.total}
                  paginate={handlePageChange}
                  pages={data.pagination.pages}
                  isComponent={agentType === "components"}
                />
              </div>
            )}
        </div>
      </div>

      <ModalsComponent
        openModal={newProjectModal}
        setOpenModal={setNewProjectModal}
        openDeleteFolderModal={false}
        setOpenDeleteFolderModal={() => {}}
        handleDeleteFolder={() => {}}
      />
    </CardsWrapComponent>
  );
};

export default HomePage;
