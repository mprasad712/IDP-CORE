import { useEffect } from "react";
import { Outlet } from "react-router-dom";
import { useGetConfig } from "@/controllers/API/queries/config/use-get-config";
import { useGetBasicExamplesQuery } from "@/controllers/API/queries/agents/use-get-basic-examples";
import { useGetFoldersQuery } from "@/controllers/API/queries/folders/use-get-folders";
import { useGetTagsQuery } from "@/controllers/API/queries/store";
import { useGetGlobalVariables } from "@/controllers/API/queries/variables";
import { useGetVersionQuery } from "@/controllers/API/queries/version";
import { ENABLE_AGENTCORE_STORE } from "@/customization/feature-flags";
import { useDarkStore } from "@/stores/darkStore";

export function AppInitPage() {
  const refreshStars = useDarkStore((state) => state.refreshStars);

  useGetVersionQuery();
  const { isFetched: isConfigFetched } = useGetConfig();
  useGetGlobalVariables();
  useGetTagsQuery({ enabled: ENABLE_AGENTCORE_STORE });
  useGetFoldersQuery();

  // Examples load in the background — pages that need them handle their own state
  const { refetch: refetchExamples } = useGetBasicExamplesQuery({ enabled: false });

  useEffect(() => {
    refreshStars();
  }, []);

  useEffect(() => {
    if (isConfigFetched) {
      refetchExamples();
    }
  }, [isConfigFetched]);

  return <Outlet />;
}
