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
  const { data } = useGetFieldConfigDocTypes(undefined, {});
  const options = data?.docTypes ?? [];

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
