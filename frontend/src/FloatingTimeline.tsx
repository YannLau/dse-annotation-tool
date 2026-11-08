import { t as tr, useI18n } from "./i18n";
import { useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent, ReactNode } from "react";

export type PanelPosition = { x: number; y: number };

export function readPanelPosition(value: unknown): PanelPosition | null {
  if (
    value !== null &&
    typeof value === "object" &&
    "x" in value &&
    "y" in value &&
    typeof value.x === "number" &&
    typeof value.y === "number" &&
    Number.isFinite(value.x) &&
    Number.isFinite(value.y)
  )
    return { x: value.x, y: value.y };
  return null;
}

const margin = 16;

export default function FloatingTimeline({
  position,
  onPositionChange,
  onHeightChange,
  children,
}: {
  position: PanelPosition | null;
  onPositionChange: (position: PanelPosition | null) => void;
  onHeightChange: (height: number) => void;
  children: ReactNode;
}) {
  useI18n();
  const panel = useRef<HTMLElement>(null);
  const drag = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    origin: PanelPosition;
    moved: boolean;
  } | null>(null);
  const [location, setLocation] = useState<PanelPosition | null>(null);
  const latestLocation = useRef(location);
  const [dragging, setDragging] = useState(false);

  function place(desired: PanelPosition | null) {
    const element = panel.current;
    if (!element) return null;
    const { width, height } = element.getBoundingClientRect();
    const maxX = Math.max(margin, window.innerWidth - width - margin);
    const maxY = Math.max(margin, window.innerHeight - height - margin);
    const next = {
      x: Math.max(
        margin,
        Math.min(maxX, desired?.x ?? (window.innerWidth - width) / 2),
      ),
      y: Math.max(margin, Math.min(maxY, desired?.y ?? maxY)),
    };
    latestLocation.current = next;
    setLocation(next);
    return next;
  }

  useLayoutEffect(() => {
    const element = panel.current!;
    const fit = () => {
      // Keep the saved coordinates intact when a smaller window temporarily
      // needs to clamp them, or additional behavior tracks increase the height.
      if (!drag.current) place(position);
      onHeightChange(element.getBoundingClientRect().height);
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(element);
    window.addEventListener("resize", fit);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", fit);
    };
  }, [position, onHeightChange]);

  function startDrag(event: PointerEvent<HTMLButtonElement>) {
    if (!event.isPrimary || event.button !== 0) return;
    const rect = panel.current!.getBoundingClientRect();
    drag.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      origin: { x: rect.x, y: rect.y },
      moved: false,
    };
    event.preventDefault();
    event.currentTarget.focus({ preventScroll: true });
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  }

  function moveDrag(event: PointerEvent<HTMLButtonElement>) {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    const dx = event.clientX - active.startX;
    const dy = event.clientY - active.startY;
    if (!active.moved && Math.hypot(dx, dy) < 3) return;
    active.moved = true;
    place({ x: active.origin.x + dx, y: active.origin.y + dy });
  }

  function finishDrag(event: PointerEvent<HTMLButtonElement>) {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    drag.current = null;
    setDragging(false);
    if (active.moved && latestLocation.current)
      onPositionChange(latestLocation.current);
  }

  function moveWithKeyboard(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "Home") {
      event.preventDefault();
      event.stopPropagation();
      place(null);
      onPositionChange(null);
      return;
    }
    const directions: Record<string, [number, number]> = {
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
      ArrowUp: [0, -1],
      ArrowDown: [0, 1],
    };
    const direction = directions[event.key];
    if (!direction || !latestLocation.current) return;
    event.preventDefault();
    event.stopPropagation();
    const step = event.shiftKey ? 50 : 10;
    const next = place({
      x: latestLocation.current.x + direction[0] * step,
      y: latestLocation.current.y + direction[1] * step,
    });
    if (next) onPositionChange(next);
  }

  return (
    <section
      ref={panel}
      className={"timeline floating-timeline" + (dragging ? " dragging" : "")}
      aria-label={tr("播放与区间控制")}
      style={{
        left: location?.x ?? margin,
        top: location?.y ?? margin,
        visibility: location ? "visible" : "hidden",
      }}
    >
      <div className="floating-timeline-header">
        <button
          type="button"
          className="timeline-drag-handle"
          aria-label={tr("拖动播放与区间控制面板")}
          title={tr("拖动调整位置；方向键微调，Shift 加速，Home 回到底部")}
          onPointerDown={startDrag}
          onPointerMove={moveDrag}
          onPointerUp={finishDrag}
          onPointerCancel={finishDrag}
          onLostPointerCapture={finishDrag}
          onKeyDown={moveWithKeyboard}
        >
          <span aria-hidden="true">⠿</span>
          {tr("播放与区间控制")}
          <small>{tr("拖动定位 · 自动记住位置")}</small>
        </button>
        <button
          type="button"
          className="timeline-reset"
          onClick={() => {
            place(null);
            onPositionChange(null);
          }}
        >
          {tr("回到底部")}
        </button>
      </div>
      <div className="floating-timeline-body">{children}</div>
    </section>
  );
}
