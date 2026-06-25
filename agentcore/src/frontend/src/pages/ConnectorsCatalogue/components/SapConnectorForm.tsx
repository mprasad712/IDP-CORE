import { Eye, EyeOff } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

export interface SapFormFields {
  base_url: string;
  auth_mode: "api_key" | "oauth2";
  api_key: string;
  oauth_token_url: string;
  client_id: string;
  client_secret: string;
}

interface Props {
  form: SapFormFields;
  onChange: (field: string, value: string) => void;
  isEditing: boolean;
}

export default function SapConnectorForm({ form, onChange, isEditing }: Props) {
  const { t } = useTranslation();
  const [showSecret, setShowSecret] = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);

  return (
    <div className="flex flex-col gap-4">
      {/* Base URL */}
      <div className="flex flex-col gap-1">
        <label className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
          {t("sap.baseUrl", "SAP OData Base URL")} *
        </label>
        <input
          type="text"
          className="input-primary"
          placeholder="https://sandbox.api.sap.com/s4hanacloud/sap/opu/odata/sap"
          value={form.base_url}
          onChange={(e) => onChange("base_url", e.target.value)}
        />
        <p className="text-xs text-zinc-500">
          {t("sap.baseUrlHint", "Sandbox: sandbox.api.sap.com — Production: your S/4HANA host")}
        </p>
      </div>

      {/* Auth Mode */}
      <div className="flex flex-col gap-1">
        <label className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
          {t("sap.authMode", "Authentication Mode")} *
        </label>
        <div className="flex gap-4">
          {(["api_key", "oauth2"] as const).map((mode) => (
            <label key={mode} className="flex items-center gap-2 cursor-pointer text-sm">
              <input
                type="radio"
                name="sap_auth_mode"
                value={mode}
                checked={form.auth_mode === mode}
                onChange={() => onChange("auth_mode", mode)}
                className="accent-primary"
              />
              {mode === "api_key"
                ? t("sap.apiKeyMode", "API Key (Sandbox)")
                : t("sap.oauth2Mode", "OAuth2 (Production)")}
            </label>
          ))}
        </div>
      </div>

      {form.auth_mode === "api_key" ? (
        /* API Key field */
        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
            {t("sap.apiKey", "API Key")} *
          </label>
          <div className="relative">
            <input
              type={showApiKey ? "text" : "password"}
              className="input-primary pr-10"
              placeholder={
                isEditing ? t("sap.apiKeyPlaceholder", "Leave blank to keep existing key") : ""
              }
              value={form.api_key}
              onChange={(e) => onChange("api_key", e.target.value)}
              autoComplete="off"
            />
            <button
              type="button"
              className="absolute right-2 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-600"
              onClick={() => setShowApiKey((v) => !v)}
            >
              {showApiKey ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
          <p className="text-xs text-zinc-500">
            {t("sap.apiKeyHint", "Register free at api.sap.com → My API Keys to get your sandbox key")}
          </p>
        </div>
      ) : (
        /* OAuth2 fields */
        <>
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
              {t("sap.tokenUrl", "OAuth2 Token URL")} *
            </label>
            <input
              type="text"
              className="input-primary"
              placeholder="https://{tenant}.authentication.{region}.hana.ondemand.com/oauth/token"
              value={form.oauth_token_url}
              onChange={(e) => onChange("oauth_token_url", e.target.value)}
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
              {t("sap.clientId", "Client ID")} *
            </label>
            <input
              type="text"
              className="input-primary"
              placeholder="sb-..."
              value={form.client_id}
              onChange={(e) => onChange("client_id", e.target.value)}
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
              {t("sap.clientSecret", "Client Secret")} *
            </label>
            <div className="relative">
              <input
                type={showSecret ? "text" : "password"}
                className="input-primary pr-10"
                placeholder={
                  isEditing
                    ? t("sap.clientSecretPlaceholder", "Leave blank to keep existing secret")
                    : ""
                }
                value={form.client_secret}
                onChange={(e) => onChange("client_secret", e.target.value)}
                autoComplete="new-password"
              />
              <button
                type="button"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-600"
                onClick={() => setShowSecret((v) => !v)}
              >
                {showSecret ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
