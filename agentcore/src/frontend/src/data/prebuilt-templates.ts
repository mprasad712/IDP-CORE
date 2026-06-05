import type { AgentType } from "@/types/agent";
import idpUniversalPipeline from "./templates/idp_universal_pipeline.json";

export const PREBUILT_TEMPLATES: AgentType[] = [
  idpUniversalPipeline,
] as unknown as AgentType[];
