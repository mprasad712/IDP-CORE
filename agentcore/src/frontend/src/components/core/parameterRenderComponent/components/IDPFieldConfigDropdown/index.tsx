import Dropdown from "../../../dropdownComponent";
import type { InputProps } from "../../types";
import { useGetFieldConfigNames } from "@/controllers/API/queries/field-configs";

export default function IDPFieldConfigDropdown({
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
  const { data } = useGetFieldConfigNames(undefined, {});
  const options = data?.names ?? [];

  const onChange = (val: any, dbValue?: boolean, skipSnapshot?: boolean) => {
    handleOnNewValue({ value: val, load_from_db: dbValue }, { skipSnapshot });
  };

  return (
    <Dropdown
      disabled={disabled}
      editNode={editNode}
      combobox
      options={options}
      nodeId={nodeId}
      nodeClass={nodeClass}
      handleNodeClass={handleNodeClass}
      onSelect={onChange}
      placeholder={placeholder ?? "Select a configuration..."}
      value={value || ""}
      id={`dropdown_idp_field_config_${id}`}
      name="config_name"
      handleOnNewValue={handleOnNewValue}
      {...baseInputProps}
    />
  );
}
