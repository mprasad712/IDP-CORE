import type { AgentType } from "@/types/agent";
import { cn } from "../../../../../utils/utils";
import { Card } from "../../../../ui/card";

const PALETTE = [
  "#D04A02", "#1B5FA8", "#2E7D32", "#6A1B9A",
  "#00695C", "#C62828", "#1565C0", "#E65100",
  "#4527A0", "#004D40", "#AD1457", "#0277BD",
];

function agentColor(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = id.charCodeAt(i) + ((h << 5) - h);
  return PALETTE[Math.abs(h) % PALETTE.length];
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}

export default function DragCardComponent({ data }: { data: AgentType }) {
  const color = agentColor(data.id);

  return (
    <Card
      draggable
      className={cn(
        "group relative flex flex-col overflow-hidden border shadow-sm",
        "transition-all duration-200 hover:shadow-lg hover:-translate-y-0.5",
        "cursor-grab active:cursor-grabbing",
      )}
    >
      {/* ── Gradient header with avatar ── */}
      <div
        className="relative flex h-[7.5rem] flex-shrink-0 items-center justify-center overflow-hidden"
        style={{
          background: `linear-gradient(145deg, ${color}1A 0%, ${color}08 100%)`,
          borderBottom: `1px solid ${color}18`,
        }}
      >
        {/* dot grid texture */}
        <div
          className="pointer-events-none absolute inset-0 opacity-[0.06]"
          style={{
            backgroundImage: `radial-gradient(circle, ${color} 1px, transparent 1px)`,
            backgroundSize: "20px 20px",
          }}
        />
        {/* glow blob */}
        <div
          className="pointer-events-none absolute -bottom-8 -right-8 h-32 w-32 rounded-full blur-3xl"
          style={{ background: `${color}25` }}
        />

        {/* avatar */}
        <div
          className="relative z-10 flex h-14 w-14 items-center justify-center rounded-2xl text-white shadow-md"
          style={{
            background: `linear-gradient(135deg, ${color} 0%, ${color}CC 100%)`,
          }}
        >
          <span className="text-lg font-bold tracking-tight select-none">
            {initials(data.name)}
          </span>
        </div>

        {/* type pill */}
        <span
          className="absolute bottom-2.5 right-2.5 z-10 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider"
          style={{ background: `${color}18`, color }}
        >
          {data.is_component ? "Component" : "Agent"}
        </span>
      </div>

      {/* ── Body ── */}
      <div className="flex flex-1 flex-col gap-1.5 px-3.5 py-3">
        <p
          className="truncate text-sm font-semibold text-foreground leading-tight"
          title={data.name}
        >
          {data.name}
        </p>

        {data.description ? (
          <p className="line-clamp-2 text-xs leading-relaxed text-muted-foreground">
            {data.description}
          </p>
        ) : (
          <p className="text-xs italic text-muted-foreground/50">No description</p>
        )}

        {(data.tags ?? []).length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {(data.tags ?? []).slice(0, 3).map((tag) => (
              <span
                key={tag}
                className="rounded-full border border-border/50 bg-muted/60 px-2 py-0.5 text-[10px] font-medium text-muted-foreground"
              >
                {tag}
              </span>
            ))}
            {(data.tags ?? []).length > 3 && (
              <span className="rounded-full border border-border/50 bg-muted/60 px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                +{(data.tags ?? []).length - 3}
              </span>
            )}
          </div>
        )}
      </div>

      {/* ── Colored bottom accent ── */}
      <div className="h-[3px] w-full flex-shrink-0" style={{ background: color }} />
    </Card>
  );
}
