import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { ColDef, ColGroupDef } from "ag-grid-community";
import ForwardedIconComponent from "@/components/common/genericIconComponent";
import PaginatorComponent from "@/components/common/paginatorComponent";
import TableComponent from "@/components/core/parameterRenderComponent/components/tableComponent";
import { Button } from "@/components/ui/button";
import { useGetTransactionsQuery } from "@/controllers/API/queries/transactions";
import useAgentsManagerStore from "@/stores/agentsManagerStore";
import { convertUTCToLocalTimezone } from "@/utils/utils";

const LogCanvasControls = () => {
  const logsOpen = useAgentsManagerStore((state) => state.logsOpen);
  const setLogsOpen = useAgentsManagerStore((state) => state.setLogsOpen);
  const currentAgentId = useAgentsManagerStore((state) => state.currentAgentId);
  const [pageIndex, setPageIndex] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [columns, setColumns] = useState<Array<ColDef | ColGroupDef>>([]);
  const [rows, setRows] = useState<any[]>([]);
  const [searchParams] = useSearchParams();
  const agentIdFromUrl = searchParams.get("id");

  const { data, isLoading, refetch } = useGetTransactionsQuery({
    id: currentAgentId ?? agentIdFromUrl,
    params: { page: pageIndex, size: pageSize },
    mode: "union",
  });

  useEffect(() => {
    if (data) {
      if (data?.rows?.length > 0) {
        data.rows.forEach((row: any) => {
          row.timestamp = convertUTCToLocalTimezone(row.timestamp);
        });
      }
      setColumns(data.columns.map((col) => ({ ...col, editable: true })));
      setRows(data.rows);
    }
  }, [data]);

  useEffect(() => {
    if (logsOpen) refetch();
  }, [logsOpen]);

  // Close on Escape, like the Playground modal.
  useEffect(() => {
    if (!logsOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setLogsOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [logsOpen, setLogsOpen]);

  const handlePageChange = useCallback((newPageIndex: number, newPageSize: number) => {
    setPageIndex(newPageIndex);
    setPageSize(newPageSize);
  }, []);

  if (!logsOpen) return null;

  // Full-window modal (like the Playground) so the execution logs are actually readable — the old
  // 240px slide-up drawer was too cramped.
  return (
    <div className="fixed inset-0 z-[60] flex flex-col bg-background">
      {/* Header */}
      <div className="flex flex-shrink-0 items-center justify-between border-b px-6 py-3">
        <div className="flex items-center gap-2">
          <ForwardedIconComponent
            name="ScrollText"
            className="h-5 w-5 text-muted-foreground"
          />
          <span className="text-base font-semibold">Execution Logs</span>
          {rows.length > 0 && (
            <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
              {data?.pagination?.total ?? rows.length}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="iconSm"
            className="h-8 w-8"
            onClick={() => refetch()}
            title="Refresh logs"
          >
            <ForwardedIconComponent name="RefreshCw" className="h-4 w-4" />
          </Button>
          <button
            onClick={() => setLogsOpen(false)}
            className="rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            title="Close (Esc)"
          >
            <ForwardedIconComponent name="X" className="h-5 w-5" />
          </button>
        </div>
      </div>

      {/* Table fills the rest of the window */}
      <div className="min-h-0 flex-1 overflow-auto p-2">
        {isLoading ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            Loading logs…
          </div>
        ) : rows.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            No executions yet
          </div>
        ) : (
          <div className="flex h-full flex-col">
            <TableComponent
              key="Executions"
              readOnlyEdit
              className="h-full w-full"
              pagination={false}
              columnDefs={columns}
              autoSizeStrategy={{ type: "fitGridWidth" }}
              rowData={rows}
              headerHeight={rows.length === 0 ? 0 : undefined}
            />
            {!isLoading && (data?.pagination?.total ?? 0) >= 10 && (
              <div className="flex flex-shrink-0 justify-end px-3 py-2">
                <PaginatorComponent
                  pageIndex={data?.pagination?.page ?? 1}
                  pageSize={data?.pagination?.size ?? 10}
                  rowsCount={[12, 24, 48, 96]}
                  totalRowsCount={data?.pagination?.total ?? 0}
                  paginate={handlePageChange}
                  pages={data?.pagination?.pages}
                />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default LogCanvasControls;
