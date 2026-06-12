import MultiselectComponent from "../multiselectComponent";
import type { InputProps } from "../../types";
import { useGetFieldConfigNames } from "@/controllers/API/queries/field-configs";

export default function IDPFieldConfigMultiselect({
  id,
  value,
  editNode,
  handleOnNewValue,
  disabled,
  ...baseInputProps
}: InputProps<string[]>) {
  const { data, isLoading } = useGetFieldConfigNames(undefined, {});
  const options = data?.names ?? [];

  if (isLoading) {
    return (
      <span className="text-sm text-muted-foreground italic">Loading field configurations…</span>
    );
  }

  return (
    <MultiselectComponent
      {...baseInputProps}
      id={`multiselect_idp_field_config_${id}`}
      value={Array.isArray(value) ? value : []}
      editNode={editNode}
      handleOnNewValue={handleOnNewValue}
      disabled={disabled}
      options={options}
      combobox={true}
    />
  );
}
