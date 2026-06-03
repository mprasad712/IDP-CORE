import type { UseMutationResult } from "@tanstack/react-query";
import type { useMutationFunctionType } from "@/types/api";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";

export type RegisterType = {
  username: string;
  email: string;
  password: string;
};

export const useRegisterUser: useMutationFunctionType<undefined, RegisterType> = (
  options?,
) => {
  const { mutate } = UseRequestProcessor();

  async function registerUserFn(body: RegisterType): Promise<{ message: string }> {
    const res = await api.post(`${getURL("REGISTER")}`, body);
    return res.data;
  }

  const mutation: UseMutationResult<{ message: string }, any, RegisterType> = mutate(
    ["useRegisterUser"],
    registerUserFn,
    options,
  );

  return mutation;
};
