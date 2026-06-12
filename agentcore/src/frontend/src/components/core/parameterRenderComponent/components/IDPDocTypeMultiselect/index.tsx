import MultiselectComponent from "../multiselectComponent";
import type { InputProps } from "../../types";
import { useGetFieldConfigDocTypes } from "@/controllers/API/queries/field-configs";

export default function IDPDocTypeMultiselect({
  id,
  value,
  editNode,
  handleOnNewValue,
  disabled,
  ...baseInputProps
}: InputProps<string[]>) {
  const { data, isLoading } = useGetFieldConfigDocTypes(undefined, {});
  const options = data?.docTypes ?? [];

  if (isLoading) {
    return (
      <span className="text-sm text-muted-foreground italic">Loading document types…</span>
    );
  }

  if (options.length === 0) {
    return (
      <MultiselectComponent
        {...baseInputProps}
        id={`multiselect_idp_doc_type_${id}`}
        value={Array.isArray(value) ? value : []}
        editNode={editNode}
        handleOnNewValue={handleOnNewValue}
        disabled={disabled}
        options={options}
        combobox={true}
        placeholder="No doc types found — set Document Type on a Field Configuration first"
      />
    );
  }

  return (
    <MultiselectComponent
      {...baseInputProps}
      id={`multiselect_idp_doc_type_${id}`}
      value={Array.isArray(value) ? value : []}
      editNode={editNode}
      handleOnNewValue={handleOnNewValue}
      disabled={disabled}
      options={options}
      combobox={false}
    />
  );
}
