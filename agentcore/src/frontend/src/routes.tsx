import { lazy, Suspense } from "react";
import {
  createBrowserRouter,
  createRoutesFromElements,
  Outlet,
  Route,
} from "react-router-dom";
import { ProtectedAdminRoute } from "./components/authorization/authAdminGuard";
import { ProtectedAccessControlRoute } from "./components/authorization/authAccessControlGuard";
import { ProtectedPermissionRoute } from "./components/authorization/permissionGuard";
import { ProtectedRoute } from "./components/authorization/authGuard";
import { ProtectedLoginRoute } from "./components/authorization/authLoginGuard";
import ContextWrapper from "./contexts";
import CustomDashboardWrapperPage from "./customization/components/custom-DashboardWrapperPage";
import { CustomNavigate } from "./customization/components/custom-navigate";
import { BASENAME } from "./customization/config-constants";
import {
  ENABLE_CUSTOM_PARAM,
  ENABLE_FILE_MANAGEMENT,
} from "./customization/feature-flags";
import { CustomRoutesStorePages } from "./customization/utils/custom-routes-store-pages";
import { AppAuthenticatedPage } from "./pages/AppAuthenticatedPage";
import { AppInitPage } from "./pages/AppInitPage";
import { AppWrapperPage } from "./pages/AppWrapperPage";
import { LoadingPage } from "./pages/LoadingPage";
import useAuthStore from "./stores/authStore";

// ─── Lazy-loaded page components ─────────────────────────────────────────────
// Each import is deferred until the route is first visited, so the initial
// bundle only contains the app shell + auth guards — not every page at once.

const LoginPage              = lazy(() => import("./pages/LoginPage"));
const CollectionPage         = lazy(() => import("./pages/MainPage/pages/main-page"));
const HomePage               = lazy(() => import("./pages/MainPage/pages/homePage"));
const FilesPage              = lazy(() => import("./pages/MainPage/pages/filesPage"));
const AgentBuilderPage       = lazy(() => import("./pages/AgentBuilderPage"));
const ViewPage               = lazy(() => import("./pages/ViewPage"));
const DashboardAdmin         = lazy(() => import("./pages/DashboardPage"));
const ModelCatalogue         = lazy(() => import("./pages/ModelCatalogue"));
const LocalModels            = lazy(() => import("./pages/LocalModels"));
const AgentCatalogueView     = lazy(() => import("./pages/AgentCatalogue"));
const WorkflowsView          = lazy(() => import("./pages/WorkflowPage"));
const ConnectorsCatalogueView = lazy(() => import("./pages/ConnectorsCatalogue"));
const ApprovalPage           = lazy(() => import("./pages/ApprovalPage"));
const ApprovalPreviewPage    = lazy(() => import("./pages/ApprovalPreviewPage"));
const TimeoutSettings        = lazy(() => import("./pages/TimeoutSettings"));
const HelpSupportPage        = lazy(() => import("./pages/SettingsPage/pages/HelpSupportPage"));
const PackagesPage           = lazy(() => import("./pages/SettingsPage/pages/PackagesPage"));
const ReleaseManagementPage  = lazy(() => import("./pages/ReleaseManagementPage"));
const AgentOrchestrator      = lazy(() => import("./pages/OrchestratorChat"));
const SharePointCallback     = lazy(() => import("./pages/SharePointCallback"));
const AdminPage              = lazy(() => import("./pages/AdminPage"));
const AccessControlPage      = lazy(() => import("./pages/AccessControlPage"));
const CostLimitsPage         = lazy(() => import("./pages/CostLimitsPage"));
const LoginAdminPage         = lazy(() => import("./pages/AdminPage/LoginPage"));
const DeleteAccountPage      = lazy(() => import("./pages/DeleteAccountPage"));
const PlaygroundPage              = lazy(() => import("./pages/Playground"));
const SetPasswordPage             = lazy(() => import("./pages/SetPasswordPage"));
const FieldConfigurationsPage     = lazy(() => import("./pages/FieldConfigurationsPage"));
const ProcessedDocsPage           = lazy(() => import("./pages/ProcessedDocsPage"));
const ReportsPage                 = lazy(() => import("./pages/ReportsPage"));
const IdpUploadPage               = lazy(() => import("./pages/IdpUploadPage"));
const ObservabilityPage           = lazy(() => import("./pages/ObservabilityPage"));
const LogsPage                    = lazy(() => import("./pages/LogsPage"));


// ─────────────────────────────────────────────────────────────────────────────

const PageLoader = () => <LoadingPage />;

function DefaultLandingRedirect() {
  const permissions = useAuthStore((state) => state.permissions);

  if (permissions.includes("view_dashboard")) {
    return <CustomNavigate replace to="dashboard-admin" />;
  }
  // IDP roles: land on the processed-docs page (review queue / approval queue / submission tracker).
  if (permissions.includes("approve_documents") || permissions.includes("review_docs")) {
    return <CustomNavigate replace to="processed-docs" />;
  }
  if (permissions.includes("submit_documents")) {
    return <CustomNavigate replace to="processed-docs" />;
  }
  if (permissions.includes("view_idp")) {
    return <CustomNavigate replace to="field-configurations" />;
  }
  if (permissions.includes("view_projects_page")) {
    return <CustomNavigate replace to="agents" />;
  }
  if (permissions.includes("view_approval_page")) {
    return <CustomNavigate replace to="approval" />;
  }
  if (permissions.includes("view_published_agents")) {
    return <CustomNavigate replace to="agent-catalogue" />;
  }
  if (permissions.includes("view_models")) {
    return <CustomNavigate replace to="model-catalogue" />;
  }
  if (permissions.includes("view_control_panel")) {
    return <CustomNavigate replace to="workflows" />;
  }
  return <CustomNavigate replace to="dashboard-admin" />;
}

const router = createBrowserRouter(
  createRoutesFromElements([
    <Route
      path="/sharepoint-callback"
      element={
        <Suspense fallback={<PageLoader />}>
          <SharePointCallback />
        </Suspense>
      }
    />,
    <Route
      path="/set-password"
      element={
        <Suspense fallback={<PageLoader />}>
          <SetPasswordPage />
        </Suspense>
      }
    />,
    <Route path="/playground/:id/">
      <Route
        path=""
        element={
          <ContextWrapper key={1}>
            <Suspense fallback={<PageLoader />}>
              <PlaygroundPage />
            </Suspense>
          </ContextWrapper>
        }
      />
    </Route>,
    <Route
      path={ENABLE_CUSTOM_PARAM ? "/:customParam?" : "/"}
      element={
        <ContextWrapper key={2}>
          <Outlet />
        </ContextWrapper>
      }
    >
      <Route path="" element={<AppInitPage />}>
        <Route path="" element={<AppWrapperPage />}>
          <Route
            path=""
            element={
              <ProtectedRoute>
                <Outlet />
              </ProtectedRoute>
            }
          >
            <Route path="" element={<AppAuthenticatedPage />}>
              <Route path="" element={<CustomDashboardWrapperPage />}>
                <Route
                  path=""
                  element={
                    <Suspense fallback={<PageLoader />}>
                      <CollectionPage />
                    </Suspense>
                  }
                >
                  <Route index element={<DefaultLandingRedirect />} />
                  <Route
                    path="help-support"
                    element={
                      <ProtectedPermissionRoute permission="view_help_support_page">
                        <Suspense fallback={<PageLoader />}><HelpSupportPage /></Suspense>
                      </ProtectedPermissionRoute>
                    }
                  />
                  <Route
                    path="approval"
                    element={
                      <ProtectedPermissionRoute permission="view_approval_page">
                        <Suspense fallback={<PageLoader />}><ApprovalPage /></Suspense>
                      </ProtectedPermissionRoute>
                    }
                  />
                  <Route
                    path="model-catalogue"
                    element={
                      <ProtectedPermissionRoute permission="view_models">
                        <Suspense fallback={<PageLoader />}><ModelCatalogue /></Suspense>
                      </ProtectedPermissionRoute>
                    }
                  />
                  <Route
                    path="local-models"
                    element={
                      <ProtectedPermissionRoute permission="view_models">
                        <Suspense fallback={<PageLoader />}><LocalModels /></Suspense>
                      </ProtectedPermissionRoute>
                    }
                  />
                  <Route
                    path="orchestrator-chat"
                    element={
                      <ProtectedPermissionRoute permission="view_orchastration_page">
                        <Suspense fallback={<PageLoader />}><AgentOrchestrator /></Suspense>
                      </ProtectedPermissionRoute>
                    }
                  />
                  <Route
                    path="connectors"
                    element={
                      <ProtectedPermissionRoute permission="view_connector_page">
                        <Suspense fallback={<PageLoader />}><ConnectorsCatalogueView /></Suspense>
                      </ProtectedPermissionRoute>
                    }
                  />
                  <Route
                    path="dashboard-admin"
                    element={
                      <ProtectedPermissionRoute permission="view_dashboard">
                        <Suspense fallback={<PageLoader />}><DashboardAdmin /></Suspense>
                      </ProtectedPermissionRoute>
                    }
                  />
                  <Route
                    path="timeout-settings"
                    element={
                      <ProtectedPermissionRoute permission="view_platform_configs">
                        <Suspense fallback={<PageLoader />}><TimeoutSettings /></Suspense>
                      </ProtectedPermissionRoute>
                    }
                  />
                  <Route
                    path="packages"
                    element={
                      <ProtectedPermissionRoute permission="view_packages_page">
                        <Suspense fallback={<PageLoader />}><PackagesPage /></Suspense>
                      </ProtectedPermissionRoute>
                    }
                  />
                  <Route
                    path="release-management"
                    element={
                      <ProtectedPermissionRoute permission="view_release_management_page">
                        <Suspense fallback={<PageLoader />}><ReleaseManagementPage /></Suspense>
                      </ProtectedPermissionRoute>
                    }
                  />
                  <Route
                    path="agent-catalogue"
                    element={
                      <ProtectedPermissionRoute permission="view_published_agents">
                        <Suspense fallback={<PageLoader />}><AgentCatalogueView /></Suspense>
                      </ProtectedPermissionRoute>
                    }
                  />
                  <Route
                    path="workflows"
                    element={
                      <ProtectedPermissionRoute permission="view_control_panel">
                        <Suspense fallback={<PageLoader />}><WorkflowsView /></Suspense>
                      </ProtectedPermissionRoute>
                    }
                  />
                  <Route
                    path="field-configurations"
                    element={
                      <ProtectedPermissionRoute permission="view_idp">
                        <Suspense fallback={<PageLoader />}><FieldConfigurationsPage /></Suspense>
                      </ProtectedPermissionRoute>
                    }
                  />
                  <Route
                    path="processed-docs"
                    element={
                      <ProtectedPermissionRoute permission="view_idp">
                        <Suspense fallback={<PageLoader />}><ProcessedDocsPage /></Suspense>
                      </ProtectedPermissionRoute>
                    }
                  />
                  <Route
                    path="idp-reports"
                    element={
                      <ProtectedPermissionRoute permission="view_idp">
                        <Suspense fallback={<PageLoader />}><ReportsPage /></Suspense>
                      </ProtectedPermissionRoute>
                    }
                  />
                  <Route
                    path="idp-upload"
                    element={
                      <ProtectedPermissionRoute permission="view_idp">
                        <Suspense fallback={<PageLoader />}><IdpUploadPage /></Suspense>
                      </ProtectedPermissionRoute>
                    }
                  />
                  <Route
                    path="idp-logs"
                    element={
                      <ProtectedPermissionRoute permission="view_idp">
                        <Suspense fallback={<PageLoader />}><LogsPage /></Suspense>
                      </ProtectedPermissionRoute>
                    }
                  />
                  <Route
                    path="observability"
                    element={
                      <ProtectedPermissionRoute permission="view_idp">
                        <Suspense fallback={<PageLoader />}><ObservabilityPage /></Suspense>
                      </ProtectedPermissionRoute>
                    }
                  />
                  {ENABLE_FILE_MANAGEMENT && (
                    <Route path="assets">
                      <Route index element={<CustomNavigate replace to="files" />} />
                      <Route
                        path="files"
                        element={
                          <Suspense fallback={<PageLoader />}><FilesPage /></Suspense>
                        }
                      />
                    </Route>
                  )}
                  <Route
                    path="agents/"
                    element={
                      <ProtectedPermissionRoute permission="view_projects_page">
                        <Outlet />
                      </ProtectedPermissionRoute>
                    }
                  >
                    <Route
                      index
                      element={
                        <Suspense fallback={<PageLoader />}><CollectionPage /></Suspense>
                      }
                    />
                    <Route
                      path="folder/:folderId"
                      element={
                        <Suspense fallback={<PageLoader />}><HomePage type="agents" /></Suspense>
                      }
                    />
                  </Route>
                  <Route
                    path="components/"
                    element={
                      <ProtectedPermissionRoute permission="view_projects_page">
                        <Suspense fallback={<PageLoader />}>
                          <HomePage key="components" type="components" />
                        </Suspense>
                      </ProtectedPermissionRoute>
                    }
                  >
                    <Route
                      path="folder/:folderId"
                      element={
                        <Suspense fallback={<PageLoader />}>
                          <HomePage key="components" type="components" />
                        </Suspense>
                      }
                    />
                  </Route>
                  <Route
                    path="all/"
                    element={
                      <ProtectedPermissionRoute permission="view_projects_page">
                        <Suspense fallback={<PageLoader />}>
                          <HomePage key="agents" type="agents" />
                        </Suspense>
                      </ProtectedPermissionRoute>
                    }
                  >
                    <Route
                      path="folder/:folderId"
                      element={
                        <Suspense fallback={<PageLoader />}>
                          <HomePage key="agents" type="agents" />
                        </Suspense>
                      }
                    />
                  </Route>
                  <Route
                    path="mcp/"
                    element={
                      <ProtectedPermissionRoute permission="view_projects_page">
                        <Suspense fallback={<PageLoader />}>
                          <HomePage key="mcp" type="mcp" />
                        </Suspense>
                      </ProtectedPermissionRoute>
                    }
                  >
                    <Route
                      path="folder/:folderId"
                      element={
                        <Suspense fallback={<PageLoader />}>
                          <HomePage key="mcp" type="mcp" />
                        </Suspense>
                      }
                    />
                  </Route>
                </Route>
                <Route
                  path="settings/*"
                  element={<CustomNavigate replace to="/" />}
                />
                {CustomRoutesStorePages()}
                <Route path="account">
                  <Route path="delete" element={<Suspense fallback={<PageLoader />}><DeleteAccountPage /></Suspense>} />
                </Route>
                <Route
                  path="admin"
                  element={
                    <ProtectedAdminRoute>
                      <Suspense fallback={<PageLoader />}><AdminPage /></Suspense>
                    </ProtectedAdminRoute>
                  }
                />
                <Route
                  path="cost-limits"
                  element={<Suspense fallback={<PageLoader />}><CostLimitsPage /></Suspense>}
                />
                <Route
                  path="access-control"
                  element={
                    <ProtectedAccessControlRoute>
                      <Suspense fallback={<PageLoader />}><AccessControlPage /></Suspense>
                    </ProtectedAccessControlRoute>
                  }
                />
              </Route>
              <Route
                path="approval/:agentId/review"
                element={<CustomDashboardWrapperPage />}
              >
                <Route
                  path=""
                  element={
                    <ProtectedPermissionRoute permission="view_approval_page">
                      <Suspense fallback={<PageLoader />}><ApprovalPreviewPage /></Suspense>
                    </ProtectedPermissionRoute>
                  }
                />
              </Route>
              <Route path="agent/:id/">
                <Route path="" element={<CustomDashboardWrapperPage />}>
                  <Route
                    path="folder/:folderId/"
                    element={<Suspense fallback={<PageLoader />}><AgentBuilderPage /></Suspense>}
                  />
                  <Route
                    path=""
                    element={<Suspense fallback={<PageLoader />}><AgentBuilderPage /></Suspense>}
                  />
                </Route>
                <Route
                  path="view"
                  element={<Suspense fallback={<PageLoader />}><ViewPage /></Suspense>}
                />
              </Route>
            </Route>
          </Route>
          <Route
            path="login"
            element={
              <ProtectedLoginRoute>
                <Suspense fallback={<PageLoader />}><LoginPage /></Suspense>
              </ProtectedLoginRoute>
            }
          />
          <Route
            path="login/admin"
            element={
              <ProtectedLoginRoute>
                <Suspense fallback={<PageLoader />}><LoginAdminPage /></Suspense>
              </ProtectedLoginRoute>
            }
          />
        </Route>
      </Route>
      <Route path="*" element={<CustomNavigate replace to="/" />} />
    </Route>,
  ]),
  { basename: BASENAME || undefined },
);

export default router;
