import { useEffect, useMemo, useState } from "react";
import Dropdown from "../../../dropdownComponent";
import type { InputProps } from "../../types";
import { api } from "@/controllers/API/api";
import { useGetConnectorCatalogue } from "@/controllers/API/queries/connectors/use-get-connector-catalogue";

const ROOT_LABEL = "/ (root)";

/**
 * Folder picker for the IDP Connector Input node's OneDrive `onedrive_folder` field.
 *
 * Resolves the connector selected in `connector_name`, then lists the account's folders
 * (root + one level of subfolders) via the OneDrive list endpoint so the user can pick a
 * folder instead of typing a path. It's a combobox, so a deeper path can still be typed.
 */
export default function OneDriveFolderDropdown({
  id,
  value,
  editNode,
  handleOnNewValue,
  disabled,
  nodeId,
  nodeClass,
  handleNodeClass,
  ...baseInputProps
}: InputProps<string>) {
  const { data: connectors } = useGetConnectorCatalogue();

  // The Connector dropdown writes the selected connector NAME into `connector_name`.
  const connectorName: string =
    (nodeClass as any)?.template?.connector_name?.value ?? "";

  const connector = useMemo(
    () =>
      (connectors ?? []).find(
        (c) =>
          c.name === connectorName &&
          c.provider === "onedrive" &&
          c.status === "connected",
      ),
    [connectors, connectorName],
  );

  const [folders, setFolders] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchFolders = async () => {
    if (!connector?.id) return;
    setLoading(true);
    setError(null);
    try {
      const out: string[] = [];
      const root = await api.post(`/api/onedrive/${connector.id}/list`, {
        folder_path: "",
        top: 200,
      });
      const rootFolders = (root.data?.items ?? [])
        .filter((i: any) => i.type === "folder")
        .map((i: any) => i.name as string);
      for (const name of rootFolders) {
        out.push(name);
        try {
          const sub = await api.post(`/api/onedrive/${connector.id}/list`, {
            folder_path: name,
            top: 200,
          });
          (sub.data?.items ?? [])
            .filter((i: any) => i.type === "folder")
            .forEach((i: any) => out.push(`${name}/${i.name}`));
        } catch {
          // ignore subfolder listing failures — keep the top-level folder
        }
      }
      setFolders(out);
    } catch (e: any) {
      setError(
        e?.response?.data?.detail ||
          "Could not list folders. Make sure the OneDrive account is linked.",
      );
      setFolders([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (connector?.id) {
      void fetchFolders();
    } else {
      setFolders([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connector?.id]);

  const options = useMemo(() => [ROOT_LABEL, ...folders], [folders]);

  const onChange = (val: any, dbValue?: boolean, skipSnapshot?: boolean) => {
    const folderValue = val === ROOT_LABEL ? "" : val;
    handleOnNewValue(
      { value: folderValue, load_from_db: dbValue },
      { skipSnapshot },
    );
  };

  const placeholder = !connectorName
    ? "Select a OneDrive connector first"
    : !connector
      ? "Connector not linked/connected"
      : loading
        ? "Loading folders..."
        : error
          ? error
          : "Select a folder...";

  return (
    <div className="flex w-full flex-col gap-1">
      <Dropdown
        disabled={disabled || !connector}
        editNode={editNode}
        options={options}
        combobox
        nodeId={nodeId}
        nodeClass={nodeClass}
        handleNodeClass={handleNodeClass}
        onSelect={onChange}
        placeholder={placeholder}
        value={value === "" ? ROOT_LABEL : value || ""}
        id={`dropdown_onedrive_folder_${id}`}
        name="onedrive_folder"
        handleOnNewValue={handleOnNewValue}
        {...baseInputProps}
      />
      {connector && (
        <button
          type="button"
          onClick={() => void fetchFolders()}
          disabled={loading}
          className="self-end text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
        >
          {loading ? "Loading folders…" : "Refresh folders"}
        </button>
      )}
    </div>
  );
}
