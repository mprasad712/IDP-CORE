/**
 * OneDrive-specific form fields for the Connector Catalogue modal.
 * Mirrors OutlookConnectorForm: client ID/secret + OAuth account linking.
 * OneDrive uses the /common authority so one connector works for both
 * personal and work/school accounts.
 */

import { Eye, EyeOff, Trash2, Loader2, RefreshCw, Link } from "lucide-react";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/controllers/API/api";

interface OneDriveFormFields {
  onedrive_client_id: string;
  onedrive_client_secret: string;
}

interface LinkedAccount {
  email: string;
  display_name: string;
  linked_at: string;
}

interface Props {
  form: OneDriveFormFields;
  onChange: (field: string, value: string) => void;
  isEditing: boolean;
  connectorId?: string;
}

export default function OneDriveConnectorForm({ form, onChange, isEditing, connectorId }: Props) {
  const { t } = useTranslation();
  const [showSecret, setShowSecret] = useState(false);
  const [accounts, setAccounts] = useState<LinkedAccount[]>([]);
  const [loadingAccounts, setLoadingAccounts] = useState(false);
  const [removingEmail, setRemovingEmail] = useState<string | null>(null);
  const [linking, setLinking] = useState(false);

  const fetchAccounts = useCallback(async () => {
    if (!connectorId) return;
    setLoadingAccounts(true);
    try {
      const res = await api.get(`/api/onedrive/${connectorId}/accounts`);
      setAccounts(res.data ?? []);
    } catch {
      setAccounts([]);
    } finally {
      setLoadingAccounts(false);
    }
  }, [connectorId]);

  useEffect(() => {
    if (isEditing && connectorId) {
      fetchAccounts();
    }
  }, [isEditing, connectorId, fetchAccounts]);

  const handleLinkAccount = async () => {
    if (!connectorId) return;
    setLinking(true);
    try {
      const res = await api.get(`/api/onedrive/${connectorId}/oauth/start`);
      const { authorize_url } = res.data;
      window.location.href = authorize_url;
    } catch {
      setLinking(false);
    }
  };

  const handleRemoveAccount = async (email: string) => {
    if (!connectorId) return;
    setRemovingEmail(email);
    try {
      await api.delete(`/api/onedrive/${connectorId}/accounts/${encodeURIComponent(email)}`);
      setAccounts((prev) => prev.filter((a) => a.email !== email));
    } catch {
      // silently fail — user can retry
    } finally {
      setRemovingEmail(null);
    }
  };

  return (
    <>
      <div>
        <label className="mb-1.5 block text-sm font-medium">{t("Client ID (App Registration)")}</label>
        <input
          value={form.onedrive_client_id}
          onChange={(e) => onChange("onedrive_client_id", e.target.value)}
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-ring focus:outline-none focus:ring-1 focus:ring-ring"
          placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
        />
      </div>
      <div>
        <label className="mb-1.5 block text-sm font-medium">
          {t("Client Secret")}{" "}
          {isEditing && (
            <span className="text-xs text-muted-foreground">{t("(leave blank to keep current)")}</span>
          )}
        </label>
        <div className="relative">
          <input
            type={showSecret ? "text" : "password"}
            value={form.onedrive_client_secret}
            onChange={(e) => onChange("onedrive_client_secret", e.target.value)}
            className="w-full rounded-lg border border-border bg-background px-3 py-2 pr-10 text-sm focus:border-ring focus:outline-none focus:ring-1 focus:ring-ring"
            placeholder={isEditing ? t("(unchanged)") : t("client-secret")}
          />
          <button
            type="button"
            onClick={() => setShowSecret(!showSecret)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          >
            {showSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        {t(
          "Uses delegated OAuth (works for personal and work/school accounts). After saving, link an account via OAuth to connect.",
        )}
      </p>

      {isEditing && connectorId && (
        <div className="mt-4 rounded-lg border border-border p-4">
          <div className="mb-3 flex items-center justify-between">
            <h4 className="text-sm font-medium">{t("Linked Accounts")}</h4>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleLinkAccount}
                disabled={linking}
                className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                {linking ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <Link className="h-3 w-3" />
                )}
                {t("Link Account")}
              </button>
              <button
                type="button"
                onClick={fetchAccounts}
                disabled={loadingAccounts}
                className="text-muted-foreground hover:text-foreground disabled:opacity-50"
                title={t("Refresh accounts")}
              >
                <RefreshCw className={`h-4 w-4 ${loadingAccounts ? "animate-spin" : ""}`} />
              </button>
            </div>
          </div>

          {loadingAccounts && accounts.length === 0 ? (
            <div className="flex items-center gap-2 py-3 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              {t("Loading accounts...")}
            </div>
          ) : accounts.length === 0 ? (
            <p className="py-3 text-sm text-muted-foreground">
              {t("No accounts linked yet. Use the OAuth flow to link one.")}
            </p>
          ) : (
            <ul className="space-y-2">
              {accounts.map((acct) => (
                <li
                  key={acct.email}
                  className="flex items-center justify-between rounded-md border border-border px-3 py-2"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{acct.email}</p>
                    {acct.display_name && (
                      <p className="truncate text-xs text-muted-foreground">{acct.display_name}</p>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => handleRemoveAccount(acct.email)}
                    disabled={removingEmail === acct.email}
                    className="ml-2 shrink-0 text-muted-foreground hover:text-destructive disabled:opacity-50"
                    title={t("Remove {{email}}", { email: acct.email })}
                  >
                    {removingEmail === acct.email ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Trash2 className="h-4 w-4" />
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </>
  );
}
