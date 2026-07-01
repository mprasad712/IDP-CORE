import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useGetRegistryModels } from "@/controllers/API/queries/models/use-get-models";
import type { ModelType } from "@/types/models/models";
import EditModelModal from "../ModelCatalogue/components/edit-model-modal";

/**
 * Local & Self-Hosted Models — a card grid over the Model Registry filtered to self-hosted
 * (OpenAI-compatible) entries tagged via provider_config.self_hosted. These are full registry
 * models (they inherit UAT/PROD, visibility, approval, RBAC) and run on the builder exactly like
 * a frontier LLM. Registration reuses the shared EditModelModal in `selfHostedMode`.
 */
export default function LocalModels() {
  const { t } = useTranslation();
  const { data: models, isLoading } = useGetRegistryModels({ active_only: false });
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ModelType | null>(null);

  const localModels = useMemo(
    () =>
      (models ?? []).filter(
        (m) =>
          m.provider === "openai_compatible" &&
          (m.provider_config as any)?.self_hosted?.is_self_hosted === true,
      ),
    [models],
  );

  const openCreate = () => {
    setEditing(null);
    setModalOpen(true);
  };
  const openEdit = (m: ModelType) => {
    setEditing(m);
    setModalOpen(true);
  };

  return (
    <div className="flex h-full flex-col gap-6 overflow-auto p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">
            {t("Local & Self-Hosted Models")}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {t(
              "Connect on-prem / locally hosted models (Ollama, vLLM, LM Studio, TGI). Use them on the builder exactly like a frontier LLM.",
            )}
          </p>
        </div>
        <Button onClick={openCreate}>{t("Register Model")}</Button>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">{t("Loading…")}</p>
      ) : localModels.length === 0 ? (
        <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">
          {t(
            'No self-hosted models yet. Click "Register Model" to connect a local LLM or SLM.',
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {localModels.map((m) => {
            const sh = ((m.provider_config as any)?.self_hosted ?? {}) as {
              kind?: string;
              fine_tuned?: boolean;
              base_model?: string | null;
            };
            const isSlm = sh.kind !== "local_llm";
            return (
              <Card
                key={m.id}
                className="cursor-pointer"
                onClick={() => openEdit(m)}
              >
                <CardHeader>
                  <div className="flex items-center justify-between gap-2">
                    <CardTitle className="truncate">{m.display_name}</CardTitle>
                    <Badge variant={sh.fine_tuned ? "emerald" : "gray"} size="sm">
                      {sh.fine_tuned ? t("Fine-tuned") : t("Base")}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="outline" size="sm">
                      {isSlm ? t("SLM") : t("Local LLM")}
                    </Badge>
                    <Badge
                      variant={m.is_active ? "successStatic" : "gray"}
                      size="sm"
                    >
                      {m.is_active ? t("Active") : t("Inactive")}
                    </Badge>
                  </div>
                  <div
                    className="truncate text-muted-foreground"
                    title={m.base_url ?? ""}
                  >
                    {m.base_url || t("(no URL)")}
                  </div>
                  <div className="text-muted-foreground">
                    {t("Model")}: <span className="font-mono">{m.model_name}</span>
                  </div>
                  {sh.fine_tuned && sh.base_model ? (
                    <div className="text-muted-foreground">
                      {t("Base model")}: {sh.base_model}
                    </div>
                  ) : null}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      <EditModelModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        model={editing}
        selfHostedMode
      />
    </div>
  );
}
