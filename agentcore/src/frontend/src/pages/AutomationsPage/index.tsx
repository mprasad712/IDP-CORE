import { Activity, Loader2, Pause, Play, RefreshCw } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useGetAllTriggers,
  type TriggerInfo,
} from "@/controllers/API/queries/triggers/use-get-all-triggers";
import {
  useRunTriggerNow,
  useToggleTrigger,
} from "@/controllers/API/queries/triggers/use-mutate-trigger";

const TYPE_LABEL: Record<string, string> = {
  email_monitor: "Email monitor",
  folder_monitor: "Folder monitor",
  schedule: "Schedule",
};

function fmtDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

function triggerSummary(t: TriggerInfo): string {
  const c = t.trigger_config || {};
  if (t.trigger_type === "email_monitor") {
    const bits: string[] = [];
    if (c.mail_folder) bits.push(`folder=${c.mail_folder}`);
    if (c.poll_interval_seconds) bits.push(`every ${c.poll_interval_seconds}s`);
    const f: string[] = [];
    if (c.filter_subject) f.push(`subject~"${c.filter_subject}"`);
    if (c.filter_body) f.push(`body~"${c.filter_body}"`);
    if (c.filter_sender) f.push(`from=${c.filter_sender}`);
    if (c.filter_to) f.push(`to=${c.filter_to}`);
    if (c.filter_cc) f.push(`cc=${c.filter_cc}`);
    if (c.unread_only) f.push("unread");
    if (f.length) bits.push(f.join(", "));
    return bits.join(" · ") || "—";
  }
  if (t.trigger_type === "folder_monitor") {
    return [c.storage_type, c.poll_interval_seconds && `every ${c.poll_interval_seconds}s`]
      .filter(Boolean)
      .join(" · ") || "—";
  }
  return c.cron || "—";
}

export default function AutomationsPage() {
  const { data, isLoading, isError } = useGetAllTriggers(undefined);
  const toggle = useToggleTrigger();
  const runNow = useRunTriggerNow();
  const [busyId, setBusyId] = useState<string | null>(null);

  const triggers = data ?? [];

  return (
    <div className="flex flex-col h-full bg-background overflow-hidden">
      <div className="flex-shrink-0 border-b px-6 py-4 bg-muted/5">
        <h1 className="text-xl font-semibold tracking-tight flex items-center gap-2">
          <Activity className="h-5 w-5 text-[#D04A02]" />
          Automations
        </h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          Background triggers from your published agents — email monitors (Outlook) auto-ingest
          matching mail into Processed Docs, folder monitors watch storage. Toggle or run them here.
        </p>
      </div>

      <div className="flex-1 overflow-auto px-6 py-5">
        {isLoading ? (
          <div className="flex items-center justify-center py-20 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin mr-2" /> Loading automations…
          </div>
        ) : isError ? (
          <div className="flex items-center justify-center py-20 text-red-600">
            Could not load automations. Please try again.
          </div>
        ) : triggers.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
            <Activity className="h-8 w-8 mb-2 opacity-40" />
            No automations yet. Publish an agent with a Connector Input (Outlook) or File Trigger node.
          </div>
        ) : (
          <div className="rounded-xl border overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/30 hover:bg-muted/30">
                  <TableHead className="font-semibold text-xs uppercase tracking-wide">Agent</TableHead>
                  <TableHead className="font-semibold text-xs uppercase tracking-wide w-36">Type</TableHead>
                  <TableHead className="font-semibold text-xs uppercase tracking-wide">Config</TableHead>
                  <TableHead className="font-semibold text-xs uppercase tracking-wide w-28">Env / Ver</TableHead>
                  <TableHead className="font-semibold text-xs uppercase tracking-wide w-28">Status</TableHead>
                  <TableHead className="font-semibold text-xs uppercase tracking-wide w-40">Last Run</TableHead>
                  <TableHead className="font-semibold text-xs uppercase tracking-wide w-16 text-center">Runs</TableHead>
                  <TableHead className="w-28 text-right" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {triggers.map((t) => (
                  <TableRow key={t.id} className="hover:bg-muted/20 transition-colors">
                    <TableCell className="font-medium text-sm">{t.agent_name || t.agent_id.slice(0, 8)}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {TYPE_LABEL[t.trigger_type] ?? t.trigger_type}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground max-w-[28rem] truncate" title={triggerSummary(t)}>
                      {triggerSummary(t)}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {t.environment}{t.version ? ` · ${t.version}` : ""}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={
                          t.is_active
                            ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                            : "bg-muted text-muted-foreground border-border"
                        }
                      >
                        {t.is_active ? "Active" : "Paused"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">{fmtDateTime(t.last_triggered_at)}</TableCell>
                    <TableCell className="text-sm text-center text-muted-foreground">{t.trigger_count}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 gap-1 rounded-lg"
                          title={t.is_active ? "Pause" : "Activate"}
                          disabled={busyId === t.id}
                          onClick={async () => {
                            setBusyId(t.id);
                            try {
                              await toggle.mutateAsync(t.id);
                            } finally {
                              setBusyId(null);
                            }
                          }}
                        >
                          {t.is_active ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 gap-1 rounded-lg"
                          title="Run now"
                          disabled={busyId === t.id}
                          onClick={async () => {
                            setBusyId(t.id);
                            try {
                              await runNow.mutateAsync(t.id);
                            } finally {
                              setBusyId(null);
                            }
                          }}
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </div>
  );
}
