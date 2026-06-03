import { ErrorBoundary } from "react-error-boundary";
import { Outlet } from "react-router-dom";
import AlertDisplayArea from "@/alerts/displayArea";
import CrashErrorComponent from "@/components/common/crashErrorComponent";
import { useHealthCheck } from "./hooks/use-health-check";

export function AppWrapperPage() {
  useHealthCheck();

  return (
    <div className="flex h-full w-full flex-col">
      <ErrorBoundary
        onReset={() => {}}
        FallbackComponent={CrashErrorComponent}
      >
        <Outlet />
      </ErrorBoundary>
      <div className="app-div">
        <AlertDisplayArea />
      </div>
    </div>
  );
}
