import { useState } from "react";
import { usePostLikeComponent } from "@/controllers/API/queries/store";
import { getComponent } from "../../../controllers/API";
import useAlertStore from "../../../stores/alertStore";
import { useStoreStore } from "../../../stores/storeStore";
import type { AgentType } from "../../../types/agent";
import type { storeComponent } from "../../../types/store";
import cloneagentWithParent, {
  getInputsAndOutputs,
} from "../../../utils/storeUtils";
import { cn } from "../../../utils/utils";
import { Button } from "../../ui/button";
import {
  Card,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "../../ui/card";
import IconComponent from "../genericIconComponent";
import ShadTooltip from "../shadTooltipComponent";
import useDataEffect from "./hooks/use-data-effect";
import useInstallComponent from "./hooks/use-handle-install";
import { convertTestName } from "./utils/convert-test-name";

export default function StoreCardComponent({
  data,
  authorized = true,
  disabled = false,
}: {
  data: storeComponent;
  authorized?: boolean;
  disabled?: boolean;
}) {
  const setSuccessData = useAlertStore((state) => state.setSuccessData);
  const setErrorData = useAlertStore((state) => state.setErrorData);
  const setValidApiKey = useStoreStore((state) => state.updateValidApiKey);
  const [loading, setLoading] = useState(false);
  const [likedByUser, setLikedByUser] = useState(data?.liked_by_user ?? false);
  const [likesCount, setLikesCount] = useState(data?.liked_by_count ?? 0);
  const [downloadsCount, setDownloadsCount] = useState(
    data?.downloads_count ?? 0,
  );

  const name = data.is_component ? "Component" : "agent";

  async function _getagentData() {
    const res = await getComponent(data.id);
    const newagent = cloneagentWithParent(res, res.id, data.is_component, true);
    return newagent;
  }

  function _hasPlayground(agent?: AgentType) {
    if (!agent) {
      return false;
    }
    const { inputs, outputs } = getInputsAndOutputs(agent?.data?.nodes ?? []);
    return inputs.length > 0 || outputs.length > 0;
  }

  useDataEffect(data, setLikedByUser, setLikesCount, setDownloadsCount);

  const { handleInstall } = useInstallComponent(
    data,
    name,
    downloadsCount,
    setDownloadsCount,
    setLoading,
    setSuccessData,
    setErrorData,
  );

  const { mutate, isPending } = usePostLikeComponent();

  const handleLikeWMutate = () => {
    if (likedByUser !== undefined || likedByUser !== null) {
      const temp = likedByUser;
      const tempNum = likesCount;
      setLikedByUser((prev) => !prev);
      setLikesCount((prev) => (temp ? prev - 1 : prev + 1));
      mutate(
        { componentId: data.id },
        {
          onSuccess: (res) => {
            setLikesCount(res.data.likes_count);
            setLikedByUser(res.data.liked_by_user);
          },
          onError: (error) => {
            setLikesCount(tempNum);
            setLikedByUser(temp);
            if (error.response.status === 403) {
              return setValidApiKey(false);
            }
            console.error(error);
            setErrorData({
              title: `Error liking ${name}.`,
              list: [error.response.data.detail],
            });
          },
        },
      );
    }
  };

  /* ── Avatar color derived from name ── */
  const PALETTE = ["#D04A02","#1B5FA8","#2E7D32","#6A1B9A","#00695C","#C62828","#1565C0","#E65100","#4527A0","#004D40"];
  const cardColor = (() => {
    let h = 0;
    const s = data.id || data.name;
    for (let i = 0; i < s.length; i++) h = s.charCodeAt(i) + ((h << 5) - h);
    return PALETTE[Math.abs(h) % PALETTE.length];
  })();
  const cardInitials = data.name.split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() ?? "").join("");

  return (
    <>
      <Card
        data-testid={`card-${convertTestName(data.name)}`}
        className={cn(
          "group relative flex flex-col overflow-hidden border shadow-sm transition-all duration-200",
          !data.is_component && !disabled && "hover:-translate-y-0.5 hover:shadow-lg cursor-pointer",
          disabled && "pointer-events-none opacity-50",
        )}
      >
        {/* ── Gradient header ── */}
        <div
          className="relative flex h-24 flex-shrink-0 items-center justify-between px-4 overflow-hidden"
          style={{
            background: `linear-gradient(145deg, ${cardColor}1A 0%, ${cardColor}08 100%)`,
            borderBottom: `1px solid ${cardColor}18`,
          }}
        >
          {/* dot texture */}
          <div
            className="pointer-events-none absolute inset-0 opacity-[0.05]"
            style={{
              backgroundImage: `radial-gradient(circle, ${cardColor} 1px, transparent 1px)`,
              backgroundSize: "18px 18px",
            }}
          />
          <div
            className="pointer-events-none absolute -bottom-6 -right-6 h-28 w-28 rounded-full blur-3xl"
            style={{ background: `${cardColor}20` }}
          />

          {/* Avatar */}
          <div
            className="relative z-10 flex h-12 w-12 items-center justify-center rounded-xl text-[13px] font-bold text-white shadow-sm"
            style={{ background: `linear-gradient(135deg, ${cardColor} 0%, ${cardColor}CC 100%)` }}
          >
            {cardInitials}
          </div>

          {/* Top-right meta */}
          <div className="relative z-10 flex flex-col items-end gap-1.5">
            <span
              className="rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider"
              style={{ background: `${cardColor}18`, color: cardColor }}
            >
              {data.is_component ? "Component" : "Agent"}
            </span>
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
              <ShadTooltip content="Likes">
                <span className="flex items-center gap-1" data-testid={`likes-${data.name}`}>
                  <IconComponent name="Heart" className={cn("h-3 w-3", likedByUser && "fill-[#D04A02] stroke-[#D04A02]")} />
                  {likesCount ?? 0}
                </span>
              </ShadTooltip>
              <ShadTooltip content="Downloads">
                <span className="flex items-center gap-1" data-testid={`downloads-${data.name}`}>
                  <IconComponent name="DownloadCloud" className="h-3 w-3" />
                  {downloadsCount ?? 0}
                </span>
              </ShadTooltip>
              {data.private && (
                <ShadTooltip content="Private">
                  <IconComponent name="Lock" className="h-3 w-3" />
                </ShadTooltip>
              )}
            </div>
          </div>
        </div>

        {/* ── Body ── */}
        <div className="flex flex-1 flex-col gap-2 px-4 py-3">
          <ShadTooltip content={data.name}>
            <h3 className="truncate text-sm font-semibold text-foreground">{data.name}</h3>
          </ShadTooltip>

          {data.user_created?.username && (
            <div className="flex items-center gap-1.5">
              <div className="flex h-4 w-4 items-center justify-center rounded-full bg-muted text-[8px] font-bold text-muted-foreground">
                {data.user_created.username.slice(0, 2).toUpperCase()}
              </div>
              <span className="text-xs text-muted-foreground">
                {data.user_created.username}
              </span>
              {data.last_tested_version && (
                <span className="rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
                  v{data.last_tested_version}
                </span>
              )}
            </div>
          )}

          <p className="line-clamp-2 text-xs leading-relaxed text-muted-foreground">
            {data.description || "No description provided."}
          </p>
        </div>

        {/* ── Footer: actions ── */}
        <div className="flex items-center justify-between border-t border-border/50 px-4 py-2.5">
          {/* Like button */}
          <ShadTooltip content={authorized ? "Like" : "Please review your API key."}>
            <Button
              disabled={isPending}
              variant="ghost"
              size="icon"
              className={cn("h-7 w-7", !authorized && "cursor-not-allowed")}
              onClick={() => { if (authorized) handleLikeWMutate(); }}
              data-testid={`like-${data.name}`}
            >
              <IconComponent
                name="Heart"
                className={cn("h-4 w-4", likedByUser ? "fill-[#D04A02] stroke-[#D04A02]" : "text-muted-foreground", !authorized && "opacity-40")}
              />
            </Button>
          </ShadTooltip>

          {/* Install button */}
          <ShadTooltip content={authorized ? "Install" : "Please review your API key."}>
            <Button
              disabled={loading || !authorized}
              variant="default"
              size="sm"
              className="h-7 gap-1.5 px-3 text-xs font-semibold"
              onClick={() => { if (!loading && authorized) handleInstall(); }}
              data-testid={`install-${data.name}`}
            >
              <IconComponent
                name={loading ? "Loader2" : "Download"}
                className={cn("h-3.5 w-3.5", loading && "animate-spin")}
              />
              {loading ? "Installing…" : "Install"}
            </Button>
          </ShadTooltip>
        </div>

        {/* bottom color bar */}
        <div className="h-[3px] w-full flex-shrink-0" style={{ background: cardColor }} />
      </Card>
    </>
  );
}
