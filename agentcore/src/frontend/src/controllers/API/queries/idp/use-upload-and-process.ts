import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";

export interface UploadResult {
  document_ids: string[];
  message: string;
}

export interface ProcessResult {
  job_id: string;
  document_id: string;
  status: string;
}

/** Upload one or more documents to an IDP agent. */
export const usePostDocumentUpload = () => {
  const queryClient = useQueryClient();

  return useMutation<UploadResult, Error, { agent_id: string; files: File[] }>({
    mutationFn: async ({ agent_id, files }) => {
      const form = new FormData();
      form.append("agent_id", agent_id);
      files.forEach((f) => form.append("files", f));
      const res = await api.post(`${getURL("IDP_DOCUMENTS")}/upload`, form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["useGetProcessedDocs"] });
    },
  });
};

/** Trigger background processing for a single uploaded document. */
export const usePostProcessDocument = () => {
  const queryClient = useQueryClient();

  return useMutation<ProcessResult, Error, { document_id: string }>({
    mutationFn: async ({ document_id }) => {
      const res = await api.post(`${getURL("IDP_DOCUMENTS")}/${document_id}/process`, {});
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["useGetProcessedDocs"] });
    },
  });
};

/** Convenience: upload a single file then immediately process it. */
export const useUploadAndProcess = () => {
  const upload = usePostDocumentUpload();
  const process = usePostProcessDocument();

  const run = async (agent_id: string, file: File): Promise<ProcessResult> => {
    const uploaded = await upload.mutateAsync({ agent_id, files: [file] });
    const document_id = uploaded.document_ids[0];
    return process.mutateAsync({ document_id });
  };

  return { run, isPending: upload.isPending || process.isPending };
};
