import { type ReactNode, useRef, useState } from "react";
import { GripVertical, X } from "lucide-react";
import { cn } from "@/utils/utils";

interface FloatingPanelProps {
  children: ReactNode;
  defaultPos?: { x: number; y: number };
  onClose: () => void;
}

export default function FloatingPanel({
  children,
  defaultPos = { x: 12, y: 52 },
  onClose,
}: FloatingPanelProps) {
  const [pos, setPos] = useState(defaultPos);
  const isDragging = useRef(false);
  const dragOffset = useRef({ x: 0, y: 0 });

  const handleDragStart = (e: React.MouseEvent) => {
    e.preventDefault();
    isDragging.current = true;
    dragOffset.current = { x: e.clientX - pos.x, y: e.clientY - pos.y };

    const onMove = (ev: MouseEvent) => {
      if (!isDragging.current) return;
      setPos({
        x: Math.max(0, ev.clientX - dragOffset.current.x),
        y: Math.max(0, ev.clientY - dragOffset.current.y),
      });
    };

    const onUp = () => {
      isDragging.current = false;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  return (
    <div
      className="absolute z-50 flex flex-col overflow-hidden rounded-xl border bg-background shadow-2xl"
      style={{
        left: pos.x,
        top: pos.y,
        width: "17.5rem",
        height: `calc(100% - ${pos.y + 8}px)`,
      }}
    >
      {/* Drag handle */}
      <div
        className="flex flex-shrink-0 cursor-grab select-none items-center justify-between border-b bg-muted/20 px-3 py-2 active:cursor-grabbing"
        onMouseDown={handleDragStart}
      >
        <div className="flex items-center gap-2">
          <GripVertical className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Components
          </span>
        </div>
        <button
          onClick={onClose}
          className="rounded p-0.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Content — fills remaining height */}
      <div className={cn("relative min-h-0 flex-1 overflow-hidden")}>
        {children}
      </div>
    </div>
  );
}
