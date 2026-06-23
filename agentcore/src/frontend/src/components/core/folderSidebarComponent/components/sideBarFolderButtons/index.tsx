import { useIsFetching, useIsMutating } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useContext } from "react";
import { useLocation, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import ForwardedIconComponent from "@/components/common/genericIconComponent";
import { AuthContext } from "@/contexts/authContext";
import { SidebarRail, SidebarTrigger } from "@/components/ui/sidebar";

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import { DEFAULT_FOLDER } from "@/constants/constants";
import { useUpdateUser } from "@/controllers/API/queries/auth";
import {
  usePatchFolders,
  usePostFolders,
  usePostUploadFolders,
} from "@/controllers/API/queries/folders";
import { useGetDownloadFolders } from "@/controllers/API/queries/folders/use-get-download-folders";
import { CustomStoreButton } from "@/customization/components/custom-store-button";
import {
  ENABLE_CUSTOM_PARAM,
  ENABLE_AGENTCORE,
  ENABLE_FILE_MANAGEMENT,
  ENABLE_KNOWLEDGE_BASES,
  ENABLE_MCP_NOTICE,
} from "@/customization/feature-flags";
import { useCustomNavigate } from "@/customization/hooks/use-custom-navigate";
import { track } from "@/customization/utils/analytics";
import { customGetDownloadFolderBlob } from "@/customization/utils/custom-get-download-folders";
import { createFileUpload } from "@/helpers/create-file-upload";
import { getObjectsFromFilelist } from "@/helpers/get-objects-from-filelist";
import useUploadAgent from "@/hooks/agents/use-upload-agent";
import { useIsMobile } from "@/hooks/use-mobile";
import useAuthStore from "@/stores/authStore";
import type { FolderType } from "../../../../../pages/MainPage/entities";
import useAlertStore from "../../../../../stores/alertStore";
import useAgentsManagerStore from "../../../../../stores/agentsManagerStore";
import { useFolderStore } from "../../../../../stores/foldersStore";
import { handleKeyDown } from "../../../../../utils/reactflowUtils";
import { cn } from "../../../../../utils/utils";
import useFileDrop from "../../hooks/use-on-file-drop";
import { SidebarFolderSkeleton } from "../sidebarFolderSkeleton";
import { HeaderButtons } from "./components/header-buttons";
import { InputEditFolderName } from "./components/input-edit-folder-name";
import { MCPServerNotice } from "./components/mcp-server-notice";
import { SelectOptions } from "./components/select-options";

// Import your logos here
// import FullLogo from "@/assets/full-logo.svg"; // Your full logo when expanded
// import CollapsedLogo from "@/assets/collapsed-logo.svg"; // Your icon/small logo when collapsed

type SideBarFoldersButtonsComponentProps = {
  handleChangeFolder?: (id: string) => void;
  handleDeleteFolder?: (item: FolderType) => void;
  handleFilesClick?: () => void;
};
const SideBarFoldersButtonsComponent = ({
  handleChangeFolder,
  handleDeleteFolder,
  handleFilesClick,
}: SideBarFoldersButtonsComponentProps) => {
  const { t } = useTranslation();
  const location = useLocation();
  const pathname = location.pathname;
  const folders = useFolderStore((state) => state.folders);
  const loading = !folders;
  const refInput = useRef<HTMLInputElement>(null);

  const _navigate = useCustomNavigate();
  
  // Get sidebar state to detect if it's collapsed
  const { open: sidebarOpen } = useSidebar();

  const currentFolder = pathname.split("/");
  const urlWithoutPath =
    pathname.split("/").length < (ENABLE_CUSTOM_PARAM ? 5 : 4);
  const checkPathFiles = pathname.includes("assets");

  const checkPathName = (itemId: string) => {
    if (urlWithoutPath && itemId === myCollectionId && !checkPathFiles) {
      return true;
    }
    return currentFolder.includes(itemId);
  };

  const setErrorData = useAlertStore((state) => state.setErrorData);
  const setSuccessData = useAlertStore((state) => state.setSuccessData);
  const isMobile = useIsMobile({ maxWidth: 1024 });
  const folderIdDragging = useFolderStore((state) => state.folderIdDragging);
  const myCollectionId = useFolderStore((state) => state.myCollectionId);
  const takeSnapshot = useAgentsManagerStore((state) => state.takeSnapshot);
  const { permissions, role } = useContext(AuthContext);

  const folderId = useParams().folderId ?? myCollectionId ?? "";

  const { dragOver, dragEnter, dragLeave, onDrop } = useFileDrop(folderId);
  const uploadAgent = useUploadAgent();
  const [foldersNames, setFoldersNames] = useState({});
  const [editFolders, setEditFolderName] = useState(
    folders.map((obj) => ({ name: obj.name, edit: false })) ?? [],
  );

  const isFetchingFolders = !!useIsFetching({
    queryKey: ["useGetFolders"],
    exact: false,
  });

  const { mutate: mutateDownloadFolder } = useGetDownloadFolders({});
  const { mutate: mutateAddFolder, isPending } = usePostFolders();
  const { mutate: mutateUpdateFolder } = usePatchFolders();
  const { mutate } = usePostUploadFolders();

  const checkHoveringFolder = (folderId: string) => {
    if (folderId === folderIdDragging) {
      return "bg-accent text-accent-foreground";
    }
  };

  const isFetchingFolder = !!useIsFetching({
    queryKey: ["useGetFolder"],
    exact: false,
  });

  const isDeletingFolder = !!useIsMutating({
    mutationKey: ["useDeleteFolders"],
  });

  const isUpdatingFolder =
    isFetchingFolders ||
    isFetchingFolder ||
    isPending ||
    loading ||
    isDeletingFolder;

  const handleUploadAgentsToFolder = () => {
    createFileUpload().then((files: File[]) => {
      if (files?.length === 0) {
        return;
      }

      getObjectsFromFilelist<any>(files).then((objects) => {
        if (objects.every((agent) => agent.data?.nodes)) {
          uploadAgent({ files }).then(() => {
            setSuccessData({
              title: "Uploaded successfully",
            });
          });
        } else {
          files.forEach((folder) => {
            const formData = new FormData();
            formData.append("file", folder);
            mutate(
              { formData },
              {
                onSuccess: () => {
                  setSuccessData({
                    title: "Project uploaded successfully.",
                  });
                },
                onError: (err) => {
                  console.error(err);
                  setErrorData({
                    title: `Error on uploading your project, try dragging it into an existing project.`,
                    list: [err["response"]["data"]["message"]],
                  });
                },
              },
            );
          });
        }
      });
    });
  };

  const handleDownloadFolder = (id: string, folderName: string) => {
    mutateDownloadFolder(
      {
        folderId: id,
      },
      {
        onSuccess: (response) => {
          customGetDownloadFolderBlob(response, id, folderName, setSuccessData);
        },
        onError: (e) => {
          setErrorData({
            title: `An error occurred while downloading your project.`,
          });
        },
      },
    );
  };

  function addNewFolder() {
    mutateAddFolder(
      {
        data: {
          name: "New Project",
          parent_id: null,
          description: "",
        },
      },
      {
        onSuccess: (folder) => {
          track("Create New Project");
          handleChangeFolder!(folder.id);
        },
      },
    );
  }

  function handleEditFolderName(e, name): void {
    const {
      target: { value },
    } = e;
    setFoldersNames((old) => ({
      ...old,
      [name]: value,
    }));
  }

  useEffect(() => {
    if (folders && folders.length > 0) {
      setEditFolderName(
        folders.map((obj) => ({ name: obj.name, edit: false })),
      );
    }
  }, [folders]);

  const handleEditNameFolder = async (item) => {
    const newEditFolders = editFolders.map((obj) => {
      if (obj.name === item.name) {
        return { name: item.name, edit: false };
      }
      return { name: obj.name, edit: false };
    });
    setEditFolderName(newEditFolders);
    if (foldersNames[item.name].trim() !== "") {
      setFoldersNames((old) => ({
        ...old,
        [item.name]: foldersNames[item.name],
      }));
      const body = {
        ...item,
        name: foldersNames[item.name],
        agents: item.agents?.length > 0 ? item.agents : [],
        components: item.components?.length > 0 ? item.components : [],
      };

      mutateUpdateFolder(
        {
          data: body,
          folderId: item.id!,
        },
        {
          onSuccess: (updatedFolder) => {
            const updatedFolderIndex = folders.findIndex(
              (f) => f.id === updatedFolder.id,
            );

            const updateFolders = [...folders];
            updateFolders[updatedFolderIndex] = updatedFolder;

            setFoldersNames({});
            setEditFolderName(
              folders.map((obj) => ({
                name: obj.name,
                edit: false,
              })),
            );
          },
        },
      );
    } else {
      setFoldersNames((old) => ({
        ...old,
        [item.name]: item.name,
      }));
    }
  };

  const handleDoubleClick = (event, item) => {
    if (item.name === DEFAULT_FOLDER) {
      return;
    }

    event.stopPropagation();
    event.preventDefault();

    handleSelectFolderToRename(item);
  };

  const handleSelectFolderToRename = (item) => {
    if (!foldersNames[item.name]) {
      setFoldersNames({ [item.name]: item.name });
    }

    if (editFolders.find((obj) => obj.name === item.name)?.name) {
      const newEditFolders = editFolders.map((obj) => {
        if (obj.name === item.name) {
          return { name: item.name, edit: true };
        }
        return { name: obj.name, edit: false };
      });
      setEditFolderName(newEditFolders);
      takeSnapshot();
      return;
    }

    setEditFolderName((old) => [...old, { name: item.name, edit: true }]);
    setFoldersNames((oldFolder) => ({
      ...oldFolder,
      [item.name]: item.name,
    }));
    takeSnapshot();
  };

  const handleKeyDownFn = (e, item) => {
    if (e.key === "Escape") {
      const newEditFolders = editFolders.map((obj) => {
        if (obj.name === item.name) {
          return { name: item.name, edit: false };
        }
        return { name: obj.name, edit: false };
      });
      setEditFolderName(newEditFolders);
      setFoldersNames({});
      setEditFolderName(
        folders.map((obj) => ({
          name: obj.name,
          edit: false,
        })),
      );
    }
    if (e.key === "Enter") {
      refInput.current?.blur();
    }
  };

  const [hoveredFolderId, setHoveredFolderId] = useState<string | null>(null);

  const userData = useAuthStore((state) => state.userData);
  const { mutate: updateUser } = useUpdateUser();
  const userDismissedMcpDialog = userData?.optins?.mcp_dialog_dismissed;

  const [isDismissedMcpDialog, setIsDismissedMcpDialog] = useState(
    userDismissedMcpDialog,
  );

  const isRootAdmin = role === "root";
  const rootVisiblePermissions = new Set([
    "view_dashboard",
    "view_platform_configs",
    "view_help_support_page",
    "view_approval_page",
    "view_packages_page",
  ]);
  const can = (permissionKey: string) =>
    isRootAdmin
      ? rootVisiblePermissions.has(permissionKey)
      : permissions?.includes(permissionKey);

  // Dispatch custom event when sidebar state changes
  useEffect(() => {
    const event = new CustomEvent("sidebar-state-change", {
      detail: { open: sidebarOpen }
    });
    window.dispatchEvent(event);
  }, [sidebarOpen]);

  const handleDismissMcpDialog = () => {
    setIsDismissedMcpDialog(true);
    updateUser({
      user_id: userData?.id!,
      user: {
        optins: {
          ...userData?.optins,
          mcp_dialog_dismissed: true,
        },
      },
    });
  };

  const handleFilesNavigation = () => {
    _navigate("/assets/files");
  };

  const handleKnowledgeNavigation = () => {
    _navigate("/assets/knowledge-bases");
  };

  /* ── Shared nav button style ── */
  const navBtn = [
    "relative w-full text-[var(--sidebar-foreground)] transition-all duration-150",
    "hover:!bg-[rgba(208,74,2,0.07)] hover:!text-[#D04A02]",
    "data-[active=true]:!bg-[rgba(208,74,2,0.12)] data-[active=true]:!text-[#D04A02]",
    "data-[active=true]:!font-semibold data-[active=true]:shadow-[inset_3px_0_0_#D04A02]",
  ].join(" ");

  /* ── Section label ── */
  const SectionLabel = ({ label }: { label: string }) =>
    sidebarOpen ? (
      <div className="px-3 pb-1 pt-5 first:pt-2">
        <span className="text-[9px] font-bold uppercase tracking-[0.14em] text-muted-foreground/40">
          {label}
        </span>
      </div>
    ) : (
      <div className="my-2 mx-3 h-px bg-border/40" />
    );

  return (
    <Sidebar
      collapsible={isMobile ? "offcanvas" : "icon"}
      data-testid="project-sidebar"
      className="bg-[var(--sidebar-background)] text-[var(--sidebar-foreground)]"
    >
      {/* ── Collapse toggle ── */}
      <div className="absolute right-[-12px] top-[56px] z-50 -translate-y-1/2">
        <SidebarTrigger className="h-6 w-6" />
      </div>

      

      {/* ── Navigation ── */}
      <SidebarContent className="text-[var(--sidebar-foreground)] py-2">
        <SidebarGroup className="px-2 py-0">
          <SidebarGroupContent>
            <SidebarMenu>

              {/* ─ Core ─ */}
              {(can("view_dashboard") || can("view_projects_page")) && (
                <SectionLabel label="Core" />
              )}

              {can("view_dashboard") && (
                <SidebarMenuItem>
                  <SidebarMenuButton size="md" isActive={pathname.startsWith("/dashboard-admin")} onClick={() => _navigate("/dashboard-admin")} className={navBtn}>
                    <ForwardedIconComponent name="LayoutDashboard" className="h-4 w-4 flex-shrink-0" />
                    {t("Dashboard")}
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}

              {can("view_projects_page") && (
                <SidebarMenuItem>
                  <SidebarMenuButton size="md" isActive={pathname.startsWith("/agents")} onClick={() => _navigate("/agents")} className={navBtn}>
                    <ForwardedIconComponent name="FolderKanban" className="h-4 w-4 flex-shrink-0" />
                    {t("Projects")}
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}

              {/* ─ Governance ─ */}
              {(can("view_approval_page") || can("view_published_agents") || can("view_models")) && (
                <SectionLabel label="Governance" />
              )}

              {can("view_approval_page") && (
                <SidebarMenuItem>
                  <SidebarMenuButton size="md" isActive={pathname.startsWith("/approval")} onClick={() => _navigate("/approval")} className={navBtn}>
                    <ForwardedIconComponent name="ClipboardCheck" className="h-4 w-4 flex-shrink-0" />
                    {t("Review & Approval")}
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}

              {can("view_published_agents") && (
                <SidebarMenuItem>
                  <SidebarMenuButton size="md" isActive={pathname.startsWith("/agent-catalogue")} onClick={() => _navigate("/agent-catalogue")} className={navBtn}>
                    <ForwardedIconComponent name="Bot" className="h-4 w-4 flex-shrink-0" />
                    {t("Agent Registry")}
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}

              {can("view_models") && (
                <SidebarMenuItem>
                  <SidebarMenuButton size="md" isActive={pathname.startsWith("/model-catalogue")} onClick={() => _navigate("/model-catalogue")} className={navBtn}>
                    <ForwardedIconComponent name="Database" className="h-4 w-4 flex-shrink-0" />
                    {t("Model Registry")}
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}

              {/* ─ Platform ─ */}
              {(can("view_control_panel") || can("view_connector_page") || can("view_packages_page")) && (
                <SectionLabel label="Platform" />
              )}

              {can("view_control_panel") && (
                <SidebarMenuItem>
                  <SidebarMenuButton size="md" isActive={pathname.startsWith("/workflows")} onClick={() => _navigate("/workflows")} className={navBtn}>
                    <ForwardedIconComponent name="PlayCircle" className="h-4 w-4 flex-shrink-0" />
                    {t("Agent Control Panel")}
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}

              {can("view_connector_page") && (
                <SidebarMenuItem>
                  <SidebarMenuButton size="md" isActive={pathname.startsWith("/connectors")} onClick={() => _navigate("/connectors")} className={navBtn}>
                    <ForwardedIconComponent name="Cable" className="h-4 w-4 flex-shrink-0" />
                    Connectors
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}

              {can("view_packages_page") && (
                <SidebarMenuItem>
                  <SidebarMenuButton size="md" isActive={pathname.startsWith("/packages")} onClick={() => _navigate("/packages")} className={navBtn}>
                    <ForwardedIconComponent name="Package" className="h-4 w-4 flex-shrink-0" />
                    {t("Packages")}
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}

              {/* ─ IDP ─ always visible to all authenticated users ─ */}
              <SectionLabel label="IDP" />

              <SidebarMenuItem>
                <SidebarMenuButton size="md" isActive={pathname.startsWith("/field-configurations")} onClick={() => _navigate("/field-configurations")} className={navBtn}>
                  <ForwardedIconComponent name="ClipboardList" className="h-4 w-4 flex-shrink-0" />
                  {t("Field Configurations")}
                </SidebarMenuButton>
              </SidebarMenuItem>

              <SidebarMenuItem>
                <SidebarMenuButton size="md" isActive={pathname.startsWith("/processed-docs")} onClick={() => _navigate("/processed-docs")} className={navBtn}>
                  <ForwardedIconComponent name="FileCheck" className="h-4 w-4 flex-shrink-0" />
                  {t("Processed Docs")}
                </SidebarMenuButton>
              </SidebarMenuItem>

              <SidebarMenuItem>
                <SidebarMenuButton size="md" isActive={pathname.startsWith("/idp-reports")} onClick={() => _navigate("/idp-reports")} className={navBtn}>
                  <ForwardedIconComponent name="BarChart3" className="h-4 w-4 flex-shrink-0" />
                  {t("Reports")}
                </SidebarMenuButton>
              </SidebarMenuItem>

              <SidebarMenuItem>
                <SidebarMenuButton size="md" isActive={pathname.startsWith("/idp-automations")} onClick={() => _navigate("/idp-automations")} className={navBtn}>
                  <ForwardedIconComponent name="Activity" className="h-4 w-4 flex-shrink-0" />
                  {t("Automations")}
                </SidebarMenuButton>
              </SidebarMenuItem>

              <SidebarMenuItem>
                <SidebarMenuButton size="md" isActive={pathname.startsWith("/idp-upload")} onClick={() => _navigate("/idp-upload")} className={navBtn}>
                  <ForwardedIconComponent name="UploadCloud" className="h-4 w-4 flex-shrink-0" />
                  {t("Document Upload")}
                </SidebarMenuButton>
              </SidebarMenuItem>

              <SidebarMenuItem>
                <SidebarMenuButton size="md" isActive={pathname.startsWith("/observability")} onClick={() => _navigate("/observability")} className={navBtn}>
                  <ForwardedIconComponent name="Activity" className="h-4 w-4 flex-shrink-0" />
                  {t("Observability")}
                </SidebarMenuButton>
              </SidebarMenuItem>

              {/* ─ System ─ */}
              {(can("view_platform_configs") || can("view_help_support_page")) && (
                <SectionLabel label="System" />
              )}

              {can("view_platform_configs") && (
                <SidebarMenuItem>
                  <SidebarMenuButton size="md" isActive={pathname.startsWith("/timeout-settings")} onClick={() => _navigate("/timeout-settings")} className={navBtn}>
                    <ForwardedIconComponent name="Clock" className="h-4 w-4 flex-shrink-0" />
                    {t("Platform Configurations")}
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}

              {can("view_help_support_page") && (
                <SidebarMenuItem>
                  <SidebarMenuButton size="md" isActive={pathname.startsWith("/help-support")} onClick={() => _navigate("/help-support")} className={navBtn}>
                    <ForwardedIconComponent name="CircleHelp" className="h-4 w-4 flex-shrink-0" />
                    {t("Help & Support")}
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}

            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      {/* ── Footer: version / brand ── */}
      {sidebarOpen && (
        <SidebarFooter className="border-t border-[rgba(208,74,2,0.08)] px-4 py-3">
          <p className="text-[9px] font-medium uppercase tracking-wider text-muted-foreground/30">
            PwC IDP © {new Date().getFullYear()}
          </p>
        </SidebarFooter>
      )}
    </Sidebar>
  );
};
export default SideBarFoldersButtonsComponent;
