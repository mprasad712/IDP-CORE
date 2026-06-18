import { useMutation } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";

export interface GenerateConfigPayload {
  description: string;
  sample_text?: string | null;
  model_id: string;
}

export interface GeneratedHeader {
  field_name: string;
  field_type: string;
  is_required: boolean;
  prompt: string | null;
  display_order: number;
}

export interface GeneratedLineItem {
  column_name: string;
  column_type: string;
  is_required: boolean;
  prompt: string | null;
  display_order: number;
}

export interface GeneratedConfig {
  suggested_name: string;
  headers: GeneratedHeader[];
  line_items: GeneratedLineItem[];
}

/** POST /field-configs/generate — draft a field configuration from a plain-language description.
 *  Returns a draft (no persistence); the caller reviews/edits and saves via the normal create flow. */
export const usePostGenerateConfig = () => {
  return useMutation<GeneratedConfig, Error, GenerateConfigPayload>({
    mutationFn: async (payload) => {
      const res = await api.post(`${getURL("IDP_FIELD_CONFIGS")}/generate`, payload);
      return res.data;
    },
  });
};
