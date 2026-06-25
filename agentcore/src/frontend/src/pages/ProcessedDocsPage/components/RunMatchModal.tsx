import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { X, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTriggerMatch, type TriggerMatchPayload } from "@/controllers/API/queries/idp/use-trigger-match";
import { api } from "@/controllers/API/api";
import { getURL } from "@/controllers/API/helpers/constants";

interface SapConnector {
  id: string;
  name: string;
  provider: string;
}

interface Props {
  documentId: string;
  defaultMatchType: "2way" | "3way";
  onClose: () => void;
  onSuccess: () => void;
}

export function RunMatchModal({ documentId, defaultMatchType, onClose, onSuccess }: Props) {
  const { t } = useTranslation();
  const { mutate: trigger, isPending } = useTriggerMatch(documentId);

  const [matchType, setMatchType] = useState<"2way" | "3way">(defaultMatchType);
  const [connectorId, setConnectorId] = useState("");
  const [amountTol, setAmountTol] = useState(2.0);
  const [qtyTol, setQtyTol] = useState(0.0);
  const [priceTol, setPriceTol] = useState(2.0);
  const [connectors, setConnectors] = useState<SapConnector[]>([]);
  const [loadingConnectors, setLoadingConnectors] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchConnectors = async () => {
      try {
        const res = await api.get(getURL("CONNECTOR_CATALOGUE"));
        const sapConns = (res.data?.connectors ?? res.data ?? []).filter(
          (c: SapConnector) => c.provider === "sap_s4hana",
        );
        setConnectors(sapConns);
        if (sapConns.length === 1) setConnectorId(sapConns[0].id);
      } catch {
        setConnectors([]);
      } finally {
        setLoadingConnectors(false);
      }
    };
    fetchConnectors();
  }, []);

  const handleRun = () => {
    if (!connectorId) {
      setError(t("match.selectConnector", "Please select a SAP connector"));
      return;
    }
    const payload: TriggerMatchPayload = {
      match_type: matchType,
      sap_connector_id: connectorId,
      amount_tolerance_pct: amountTol,
      quantity_tolerance_pct: qtyTol,
      unit_price_tolerance_pct: priceTol,
    };
    trigger(payload, {
      onSuccess: () => onSuccess(),
      onError: (err) => setError(err.message),
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-background rounded-xl border shadow-xl w-[460px] max-w-full mx-4">
        <div className="flex items-center justify-between px-5 py-4 border-b">
          <h2 className="text-sm font-semibold">
            {t("match.runMatch", "Run SAP Match")}
          </h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <X size={16} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {/* Match Type */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-zinc-600">
              {t("match.type", "Match Type")}
            </label>
            <div className="flex gap-3">
              {(["2way", "3way"] as const).map((mt) => (
                <label key={mt} className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="radio"
                    value={mt}
                    checked={matchType === mt}
                    onChange={() => setMatchType(mt)}
                    className="accent-primary"
                  />
                  {mt === "2way"
                    ? t("match.twoWay", "2-Way (Invoice vs PO)")
                    : t("match.threeWay", "3-Way (Invoice vs PO vs GR)")}
                </label>
              ))}
            </div>
          </div>

          {/* SAP Connector */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-zinc-600">
              {t("match.sapConnector", "SAP Connector")} *
            </label>
            {loadingConnectors ? (
              <div className="text-xs text-zinc-400 flex items-center gap-1">
                <RefreshCw size={12} className="animate-spin" />
                {t("match.loadingConnectors", "Loading connectors…")}
              </div>
            ) : connectors.length === 0 ? (
              <p className="text-xs text-red-500">
                {t("match.noSapConnectors", "No SAP S/4HANA connectors found. Add one in the Connectors Catalogue.")}
              </p>
            ) : (
              <select
                value={connectorId}
                onChange={(e) => setConnectorId(e.target.value)}
                className="h-8 rounded-md border text-sm px-2 bg-background"
              >
                <option value="">{t("match.selectConnector", "Select connector…")}</option>
                {connectors.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            )}
          </div>

          {/* Tolerances */}
          <div className="grid grid-cols-3 gap-3">
            {[
              { label: t("match.amountTol", "Amount ±%"), value: amountTol, set: setAmountTol },
              { label: t("match.qtyTol", "Qty ±%"), value: qtyTol, set: setQtyTol },
              { label: t("match.priceTol", "Unit Price ±%"), value: priceTol, set: setPriceTol },
            ].map(({ label, value, set }) => (
              <div key={label} className="flex flex-col gap-1">
                <label className="text-xs text-zinc-500">{label}</label>
                <input
                  type="number"
                  min={0}
                  max={100}
                  step={0.5}
                  value={value}
                  onChange={(e) => set(parseFloat(e.target.value) || 0)}
                  className="h-7 rounded border text-sm px-2 bg-background"
                />
              </div>
            ))}
          </div>

          {error && <p className="text-xs text-red-500">{error}</p>}
        </div>

        <div className="flex justify-end gap-2 px-5 py-4 border-t">
          <Button variant="outline" size="sm" onClick={onClose}>
            {t("cancel", "Cancel")}
          </Button>
          <Button
            size="sm"
            onClick={handleRun}
            disabled={isPending || loadingConnectors}
            className="bg-[#D04A02] hover:bg-[#B84000] text-white"
          >
            {isPending ? (
              <><RefreshCw size={12} className="animate-spin mr-1" /> {t("match.running", "Running…")}</>
            ) : (
              t("match.run", "Run Match")
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
