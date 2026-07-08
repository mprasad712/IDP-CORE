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

/**
 * Which agent version a document runs against.
 * Omitted -> the backend defaults to "dev" = the draft canvas (what the Playground wants).
 * "uat"/"prod" -> the PUBLISHED deployment snapshot, so the run is immune to later canvas edits.
 */
export interface RunTarget {
  env?: string;
  /** e.g. "v2". Omitted -> the currently active published version. */
  version?: string;
}

/** Upload one or more documents to an IDP agent. */
export const usePostDocumentUpload = () => {
  const queryClient = useQueryClient();

  return useMutation<
    UploadResult,
    Error,
    { agent_id: string; files: File[] } & RunTarget
  >({
    mutationFn: async ({ agent_id, files, env, version }) => {
      const form = new FormData();
      form.append("agent_id", agent_id);
      // Only send when set — an empty string is not a valid env and would 400.
      if (env) form.append("env", env);
      if (version) form.append("version", version);
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

export interface CancelResult {
  document_id: string;
  cancelled: boolean;
  status: string;
}

/** Cancel a running/queued IDP document process (the Stop button). */
export const usePostCancelDocument = () => {
  const queryClient = useQueryClient();

  return useMutation<CancelResult, Error, { document_id: string }>({
    mutationFn: async ({ document_id }) => {
      const res = await api.post(
        `${getURL("IDP_DOCUMENTS")}/${document_id}/cancel`,
        {},
      );
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

  /**
   * `target` is optional: callers that omit it (the Playground) keep running the draft.
   * The env/version ride on the uploaded document row, so /process needs no extra argument.
   */
  const run = async (
    agent_id: string,
    file: File,
    target?: RunTarget,
  ): Promise<ProcessResult> => {
    const uploaded = await upload.mutateAsync({
      agent_id,
      files: [file],
      ...target,
    });
    const document_id = uploaded.document_ids[0];
    return process.mutateAsync({ document_id });
  };

  return { run, isPending: upload.isPending || process.isPending };
};
