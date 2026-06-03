import { useMemo } from "react";
import { getRuntimeEnv } from "@/utils/runtime-env";

/**
 * Hook to check if the current window is being accessed through a local connection
 * @returns A boolean indicating if the current connection is local
 */
export function useCustomIsLocalConnection(): boolean {
  return useMemo(() => {
    // Get the current window's hostname
    const currentHostname = window.location.hostname;

    // List of hostnames/IPs that are considered local
    const localHost =
      getRuntimeEnv("HOST_IP") ||
      getRuntimeEnv("LOCALHOST_HOST") ||
      window.location.hostname;
    const localAddresses = [localHost, "127.0.0.1", "0.0.0.0"];

    // Check if the current hostname is in the local addresses list
    return localAddresses.includes(currentHostname);
  }, []);
}
