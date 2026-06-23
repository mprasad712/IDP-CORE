import Dropdown from "../../../dropdownComponent";
import type { InputProps } from "../../types";
import { useGetConnectorCatalogue } from "@/controllers/API/queries/connectors/use-get-connector-catalogue";

export default function IDPConnectorDropdown({
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
  const { data: connectors } = useGetConnectorCatalogue(undefined, {
    refetchOnMount: true,
  });

  const options = (connectors ?? [])
    .filter(
      (c) =>
        c.status === "connected" &&
        (c.provider === "outlook" ||
          c.provider === "sharepoint" ||
          c.provider === "onedrive"),
    )
    .map((c) => c.name);

  const onChange = (val: any, dbValue?: boolean, skipSnapshot?: boolean) => {
    // Record the selected connector's provider so provider-specific fields render via show_when:
    // email-only fields show for "outlook"; SharePoint folder/library fields show for "sharepoint".
    const provider =
      (connectors ?? []).find((c) => c.name === val)?.provider ?? "";
    if (handleNodeClass && (nodeClass as any)?.template?.connector_provider) {
      handleNodeClass({
        ...(nodeClass as any),
        template: {
          ...(nodeClass as any).template,
          connector_name: {
            ...(nodeClass as any).template.connector_name,
            value: val,
            load_from_db: dbValue,
          },
          connector_provider: {
            ...(nodeClass as any).template.connector_provider,
            value: provider,
          },
        },
      });
    } else {
      // Legacy node without a connector_provider field — preserve original behaviour.
      handleOnNewValue({ value: val, load_from_db: dbValue }, { skipSnapshot });
    }
  };

  return (
    <Dropdown
      disabled={disabled}
      editNode={editNode}
      options={options}
      nodeId={nodeId}
      nodeClass={nodeClass}
      handleNodeClass={handleNodeClass}
      onSelect={onChange}
      placeholder={placeholder ?? "Select a connector..."}
      value={value || ""}
      id={`dropdown_idp_connector_${id}`}
      name="connector_name"
      handleOnNewValue={handleOnNewValue}
      {...baseInputProps}
    />
  );
}
