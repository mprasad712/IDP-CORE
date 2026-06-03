import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactFlowProvider } from "@xyflow/react";
import type { ReactNode } from "react";
import { useState } from "react";
import { GradientWrapper } from "@/components/common/GradientWrapper";
import { CustomWrapper } from "@/customization/custom-wrapper";
import { TooltipProvider } from "../components/ui/tooltip";
import { ApiInterceptor } from "../controllers/API/api";
import { AuthProvider } from "./authContext";

export default function ContextWrapper({ children }: { children: ReactNode }) {
  // QueryClient must be stable across renders — creating it inside the function
  // body caused every re-render to discard the entire query cache.
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 1000 * 30,
          },
        },
      }),
  );

  return (
    <>
      <CustomWrapper>
        <GradientWrapper>
          <QueryClientProvider client={queryClient}>
            <AuthProvider>
              <TooltipProvider skipDelayDuration={0}>
                <ReactFlowProvider>
                  <ApiInterceptor />
                  {children}
                </ReactFlowProvider>
              </TooltipProvider>
            </AuthProvider>
          </QueryClientProvider>
        </GradientWrapper>
      </CustomWrapper>
    </>
  );
}
