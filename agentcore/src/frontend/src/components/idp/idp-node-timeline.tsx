import IconComponent from "@/components/common/genericIconComponent";
import Loading from "@/components/ui/loading";
import { cn } from "@/utils/utils";

export interface IdpProgressNode {
  id: string;
  name: string;
  status: "pending" | "running" | "done" | "failed" | "skipped" | string;
}

const STATUS_META: Record<string, { icon: string; className: string }> = {
  done: { icon: "CircleCheck", className: "text-emerald-500" },
  failed: { icon: "CircleX", className: "text-destructive" },
  skipped: { icon: "MinusCircle", className: "text-muted-foreground/50" },
  pending: { icon: "Circle", className: "text-muted-foreground/40" },
};

/** Live per-node timeline for an IDP document run: each node shows pending → running → done/failed. */
export function IdpNodeTimeline({
  nodes,
  className,
}: {
  nodes: IdpProgressNode[];
  className?: string;
}) {
  if (!nodes?.length) return null;
  return (
    <div className={cn("flex w-full flex-col gap-0.5", className)}>
      {nodes.map((n) => {
        const meta = STATUS_META[n.status] ?? STATUS_META.pending;
        const dim = n.status === "pending" || n.status === "skipped";
        return (
          <div
            key={n.id}
            className={cn(
              "flex items-center gap-2 rounded-md px-2 py-1 text-sm transition-colors",
              n.status === "running" && "bg-primary/5",
            )}
          >
            {n.status === "running" ? (
              <Loading className="h-4 w-4 shrink-0 text-primary" />
            ) : (
              <IconComponent
                name={meta.icon}
                className={cn("h-4 w-4 shrink-0", meta.className)}
                strokeWidth={2}
              />
            )}
            <span
              className={cn(
                "truncate",
                dim ? "text-muted-foreground" : "text-foreground",
                n.status === "running" && "font-medium",
              )}
            >
              {n.name}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export default IdpNodeTimeline;
