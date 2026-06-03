import {
  type QueryClient,
  type UseMutationOptions,
  type UseQueryOptions,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import type {
  MutationFunctionType,
  QueryFunctionType,
} from "../../../types/api";

const isAuthenticationError = (error: unknown) => {
  const status = (error as { response?: { status?: number } })?.response?.status;
  return status === 401 || status === 403;
};

const isServerError = (error: unknown) => {
  const status = (error as { response?: { status?: number } })?.response?.status;
  return !!status && status >= 500;
};

export function UseRequestProcessor(): {
  query: QueryFunctionType;
  mutate: MutationFunctionType;
  queryClient: QueryClient;
} {
  const queryClient = useQueryClient();

  function query(
    queryKey: UseQueryOptions["queryKey"],
    queryFn: UseQueryOptions["queryFn"],
    options: Omit<UseQueryOptions, "queryFn" | "queryKey"> = {},
  ) {
    return useQuery({
      queryKey,
      queryFn,
      retry: (failureCount, error) => {
        if (isAuthenticationError(error)) return false;
        if (isServerError(error)) return false; // server errors don't benefit from retrying
        return failureCount < 2; // reduced from 5 — network blips only
      },
      retryDelay: (attemptIndex) => Math.min(500 * 2 ** attemptIndex, 3000), // max 3s, down from 30s
      ...options,
    });
  }

  function mutate(
    mutationKey: UseMutationOptions["mutationKey"],
    mutationFn: UseMutationOptions["mutationFn"],
    options: Omit<UseMutationOptions, "mutationFn" | "mutationKey"> = {},
  ) {
    return useMutation({
      mutationKey,
      mutationFn,
      onSettled: (data, error, variables, context) => {
        queryClient.invalidateQueries({ queryKey: mutationKey });
        options.onSettled && options.onSettled(data, error, variables, context);
      },
      ...options,
      retry:
        options.retry ??
        ((failureCount, error) => {
          if (isAuthenticationError(error)) return false;
          if (isServerError(error)) return false; // fail fast on 5xx — no point retrying
          return failureCount < 1; // one retry for transient network errors only
        }),
      retryDelay: () => 500, // flat 500ms for the single allowed retry
    });
  }

  return { query, mutate, queryClient };
}
