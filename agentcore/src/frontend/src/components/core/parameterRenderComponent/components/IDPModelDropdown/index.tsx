import Dropdown from "../../../dropdownComponent";
import type { InputProps } from "../../types";
import { useGetRegistryModels } from "@/controllers/API/queries/models/use-get-models";

/**
 * On-node model picker for IDP nodes (flag `idp_model_fetch`). Lists all agent-visible LLM
 * models from the Model Registry — frontier, local LLMs, and SLMs alike — and stores the
 * selection in the app-standard "display | model_name | uuid" string so the backend
 * (`_direct_model_uuid` in services/idp/agent_config.py) parses the UUID from the LAST
 * `|`-segment. The display half carries readable kind / fine-tuned badges.
 */
export default function IDPModelDropdown({
  id,
  value,
  editNode,
  handleOnNewValue,
  disabled,
  placeholder,
  nodeId,
  nodeClass,
  handleNodeClass,
  ...baseInputProps
}: InputProps<string>) {
  const { data: models } = useGetRegistryModels({ active_only: true });

  const agentModels = (models ?? []).filter(
    (m) =>
      m.model_type === "llm" &&
      (m.show_in ?? ["orchestrator", "agent"]).includes("agent"),
  );

  const toOption = (m: (typeof agentModels)[number]): string => {
    const sh =
      m.provider === "openai_compatible"
        ? (m.provider_config?.self_hosted as
            | { is_self_hosted?: boolean; kind?: string; fine_tuned?: boolean }
            | undefined)
        : undefined;
    let tag = "";
    if (sh?.is_self_hosted) {
      tag = sh.kind === "local_llm" ? " · Local LLM" : " · SLM";
      if (sh.fine_tuned) tag += " · Fine-tuned";
    }
    return `${m.display_name}${tag} (${m.provider}) | ${m.model_name} | ${m.id}`;
  };
  const options = agentModels.map(toOption);
  // Keep the persisted selection in the list so the shared Dropdown never auto-clears the saved
  // model_id while the registry query is still loading (or if the model was later filtered out).
  const optionsWithValue =
    value && !options.includes(value) ? [value, ...options] : options;

  return (
    <Dropdown
      disabled={disabled}
      editNode={editNode}
      options={optionsWithValue}
      nodeId={nodeId}
      nodeClass={nodeClass}
      handleNodeClass={handleNodeClass}
      onSelect={(val: any, dbValue?: boolean, skipSnapshot?: boolean) =>
        handleOnNewValue({ value: val, load_from_db: dbValue }, { skipSnapshot })
      }
      placeholder={placeholder ?? "Select a model..."}
      value={value || ""}
      id={`dropdown_idp_model_${id}`}
      name="model_id"
      handleOnNewValue={handleOnNewValue}
      {...baseInputProps}
    />
  );
}
