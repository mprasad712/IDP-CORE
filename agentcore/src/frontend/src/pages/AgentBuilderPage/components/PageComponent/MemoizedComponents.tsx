import { Background } from "@xyflow/react";
import { memo } from "react";
import { useShallow } from "zustand/react/shallow";
import ForwardedIconComponent from "@/components/common/genericIconComponent";
import CanvasControls from "@/components/core/canvasControlsComponent/CanvasControls";
import LogCanvasControls from "@/components/core/logCanvasControlsComponent";
import { Button } from "@/components/ui/button";
import useAgentStore from "@/stores/agentStore";
import { cn } from "@/utils/utils";

export const MemoizedBackground = memo(() => (
  <Background size={2} gap={20} className="" />
));

interface MemoizedCanvasControlsProps {
  setIsAddingNote: (value: boolean) => void;
  shadowBoxWidth: number;
  shadowBoxHeight: number;
}

export const MemoizedLogCanvasControls = memo(() => <LogCanvasControls />);

export const MemoizedCanvasControls = memo(
  ({
    setIsAddingNote,
    shadowBoxWidth,
    shadowBoxHeight,
  }: MemoizedCanvasControlsProps) => {
    const isLocked = useAgentStore(
      useShallow((state) => state.currentAgent?.locked),
    );

    return (
      <CanvasControls>
        <Button
          unstyled
          unselectable="on"
          size="icon"
          data-testid="lock-status"
          className="flex items-center justify-center px-2 rounded-none gap-1 cursor-default"
          title={`Lock status: ${isLocked ? "Locked" : "Unlocked"}`}
        >
          <ForwardedIconComponent
            name={isLocked ? "Lock" : "Unlock"}
            className={cn(
              "!h-[18px] !w-[18px] text-muted-foreground",
              isLocked && "text-destructive",
            )}
          />
          {isLocked && (
            <span className="text-xs text-destructive">Agent Locked</span>
          )}
        </Button>
      </CanvasControls>
    );
  },
);

// Sidebar trigger is now in the toolbar pill — this component is no longer used.
export const MemoizedSidebarTrigger = memo(() => null);
