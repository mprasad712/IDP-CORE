import { Panel } from "@xyflow/react";
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { ColDef, ColGroupDef } from "ag-grid-community";
import ForwardedIconComponent from "@/components/common/genericIconComponent";
import PaginatorComponent from "@/components/common/paginatorComponent";
import TableComponent from "@/components/core/parameterRenderComponent/components/tableComponent";
import { Button } from "@/components/ui/button";
import { useGetTransactionsQuery } from "@/controllers/API/queries/transactions";
import useAgentsManagerStore from "@/stores/agentsManagerStore";
import { cn, convertUTCToLocalTimezone } from "@/utils/utils";

const LogCanvasControls = () => {
  const [logsOpen, setLogsOpen] = useState(false);
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

  const handlePageChange = useCallback((newPageIndex: number, newPageSize: number) => {
    setPageIndex(newPageIndex);
    setPageSize(newPageSize);
  }, []);

  return (
    <>
      {/* Toggle button — bottom-left corner */}
      <Panel
        data-testid="canvas_controls"
        className="react-flow__controls !m-2 rounded-md"
        position="bottom-left"
      >
        <Button
          variant="primary"
          size="sm"
          className="flex items-center !gap-1.5"
          onClick={() => setLogsOpen((v) => !v)}
        >
          <ForwardedIconComponent name="Terminal" className="text-primary" />
          <span className="text-mmd font-normal">Logs</span>
          <ForwardedIconComponent
            name={logsOpen ? "ChevronDown" : "ChevronUp"}
            className="h-3 w-3 text-primary"
          />
        </Button>
      </Panel>

      {/* Slide-up drawer — spans full width at bottom of canvas */}
      <Panel
        position="bottom-left"
        className="!left-0 !right-0 !bottom-0 !m-0 !w-full pointer-events-none"
      >
        <div
          className={cn(
            "pointer-events-auto w-full border-t bg-background/95 backdrop-blur-sm transition-all duration-200 ease-in-out",
            logsOpen ? "h-60" : "h-0 overflow-hidden",
          )}
        >
          {logsOpen && (
            <>
              {/* Drawer header */}
              <div className="flex flex-shrink-0 items-center justify-between border-b px-4 py-2">
                <div className="flex items-center gap-2">
                  <ForwardedIconComponent
                    name="ScrollText"
                    className="h-4 w-4 text-muted-foreground"
                  />
                  <span className="text-sm font-semibold">Execution Logs</span>
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
                    className="h-7 w-7"
                    onClick={() => refetch()}
                    title="Refresh logs"
                  >
                    <ForwardedIconComponent name="RefreshCw" className="h-3.5 w-3.5" />
                  </Button>
                  <button
                    onClick={() => setLogsOpen(false)}
                    className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                  >
                    <ForwardedIconComponent name="X" className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>

              {/* Table */}
              <div className="h-[calc(100%-41px)] overflow-auto">
                {isLoading ? (
                  <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                    Loading logs…
                  </div>
                ) : rows.length === 0 ? (
                  <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                    No executions yet
                  </div>
                ) : (
                  <>
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
                      <div className="flex justify-end px-3 py-2">
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
                  </>
                )}
              </div>
            </>
          )}
        </div>
      </Panel>
    </>
  );
};

export default LogCanvasControls;
