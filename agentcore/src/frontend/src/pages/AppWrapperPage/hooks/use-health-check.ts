import { useEffect, useRef, useState } from "react";
import type { AxiosError } from "axios";
import { useIsFetching, useIsMutating } from "@tanstack/react-query";
import { useGetHealthQuery } from "@/controllers/API/queries/health";
import useAgentStore from "@/stores/agentStore";
import useAgentsManagerStore from "@/stores/agentsManagerStore";
import { useUtilityStore } from "@/stores/utilityStore";
import useAlertStore from "@/stores/alertStore";

export function useHealthCheck() {
  const healthCheckMaxRetries = useAgentsManagerStore(
    (state) => state.healthCheckMaxRetries,
  );
  const healthCheckTimeout = useUtilityStore(
    (state) => state.healthCheckTimeout,
  );
  const setErrorData = useAlertStore((state) => state.setErrorData);

  const isMutating = useIsMutating();
  const isFetching = useIsFetching({
    predicate: (query) => query.queryKey[0] !== "useGetHealthQuery",
  });
  const isBuilding = useAgentStore((state) => state.isBuilding);

  const disabled = isMutating || isFetching || isBuilding;

  const {
    isFetching: fetchingHealth,
    isError: isErrorHealth,
    error,
    refetch,
  } = useGetHealthQuery({ enableInterval: !disabled });

  const [retryCount, setRetryCount] = useState(0);
  const prevHealthTimeout = useRef<string | null>(null);

  // Show a toast when backend becomes unreachable — don't block the UI
  useEffect(() => {
    if (
      healthCheckTimeout !== null &&
      healthCheckTimeout !== prevHealthTimeout.current
    ) {
      if (healthCheckTimeout === "serverDown") {
        setErrorData({
          title: "Backend unreachable",
          list: ["Some features may not work. Check your server connection."],
        });
      } else if (healthCheckTimeout === "timeout") {
        setErrorData({
          title: "Backend connection timed out",
          list: ["The server is taking too long to respond."],
        });
      }
    }
    prevHealthTimeout.current = healthCheckTimeout;
  }, [healthCheckTimeout, setErrorData]);

  useEffect(() => {
    const isServerBusy =
      (error as AxiosError)?.response?.status === 503 ||
      (error as AxiosError)?.response?.status === 429;

    if (isServerBusy && isErrorHealth && !disabled) {
      const maxRetries = healthCheckMaxRetries;
      if (retryCount < maxRetries) {
        const delay = 2 ** retryCount * 1000;
        const timer = setTimeout(() => {
          refetch();
          setRetryCount(retryCount + 1);
        }, delay);

        return () => clearTimeout(timer);
      }
    } else {
      setRetryCount(0);
    }
  }, [isErrorHealth, retryCount, refetch]);

  return { refetch, fetchingHealth };
}
