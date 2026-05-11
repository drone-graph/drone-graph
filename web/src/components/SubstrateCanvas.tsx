import {
  createEffect,
  createSignal,
  onCleanup,
  onMount,
} from "solid-js";
import {
  forceCenter,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceY,
} from "d3-force";

import { ContextMenu } from "./ContextMenu";
import { api } from "../api";
import { focusDrone, selectGap, store } from "../state";
import type { ActiveDrone, Finding, Gap } from "../types";

// ---- Node / edge types -----------------------------------------------------

interface SimNode {
  id: string;
  gap: Gap;
  x: number;
  y: number;
  vx: number;
  vy: number;
  fx?: number | null;
  fy?: number | null;
}

interface SimEdge {
  source: SimNode | string;
  target: SimNode | string;
}

// ---- Component -------------------------------------------------------------

const HEARTBEAT_MS = 800;

export function SubstrateCanvas() {
  let canvasRef: HTMLCanvasElement | undefined;
  let containerRef: HTMLDivElement | undefined;
  const [size, setSize] = createSignal<[number, number]>([800, 600]);
  const [transform, setTransform] = createSignal<{
    k: number;
    x: number;
    y: number;
  }>({ k: 1, x: 0, y: 0 });
  const [hoverGapId, setHoverGapId] = createSignal<string | null>(null);
  const [menu, setMenu] = createSignal<{
    x: number;
    y: number;
    gap: Gap | null;
  } | null>(null);
  const [recentFinding, setRecentFinding] = createSignal<Finding | null>(null);

  // ---- Layout state held outside Solid reactivity ------------------------

  let nodes: SimNode[] = [];
  let edges: SimEdge[] = [];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let sim: any = null;

  // Sediment band: collapsed retired gaps render here as dim dots
  // (fading-but-retrievable per the design brief).
  let sedimentExpanded = false;

  function buildSim(): void {
    const [w, h] = size();
    const gapsById = new Map<string, Gap>();
    for (const g of store.gaps) gapsById.set(g.id, g);

    // Filter: by default we collapse very-old retired gaps into the sediment
    // band. "Old" = retired for > 60 ticks worth of staleness (we don't have
    // tick-of-retire, so use plain "retired" + sediment toggle).
    const visible: Gap[] = [];
    const buried: Gap[] = [];
    for (const g of store.gaps) {
      if (g.status === "retired" && !sedimentExpanded) {
        buried.push(g);
      } else {
        visible.push(g);
      }
    }

    // Canvas anchors — preset row sits in the upper band, emergent gaps
    // cluster a fixed-ish distance below the presets (not centered on the
    // canvas) so a tall viewport doesn't leave a big dead zone between
    // them. Clamped so on short canvases the cluster stays away from the
    // bottom and on tall ones it stays close to the presets.
    const presetY = Math.max(60, h * 0.12);
    const emergentCenterY = Math.min(h * 0.55, presetY + 220);

    // Reuse positions for nodes we've already laid out.
    const prev = new Map(nodes.map((n) => [n.id, n]));
    nodes = visible.map((g, i) => {
      const p = prev.get(g.id);
      // Seed emergent nodes in a tight ring around the emergent-cluster
      // center rather than on a wide circle around the full canvas — keeps
      // them close to their eventual settling point, no big drift.
      const angle = (i / Math.max(1, visible.length)) * Math.PI * 2;
      const r = 100;
      return {
        id: g.id,
        gap: g,
        x: p?.x ?? w / 2 + Math.cos(angle) * r,
        y: p?.y ?? emergentCenterY + Math.sin(angle) * r,
        vx: p?.vx ?? 0,
        vy: p?.vy ?? 0,
        fx: null,
        fy: null,
      };
    });

    // Pin preset gaps to the top in fixed orbits.
    let presetX = 0;
    for (const n of nodes) {
      if (n.gap.preset_kind) {
        presetX += 1;
        n.fx = (w / 4) * presetX;
        n.fy = presetY;
      }
    }

    const ids = new Set(nodes.map((n) => n.id));
    edges = store.parent_edges
      .filter(([p, c]) => ids.has(p) && ids.has(c))
      .map(([p, c]) => ({ source: p, target: c }));

    if (sim !== null) sim.stop();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    sim = forceSimulation(nodes as any)
      .force(
        "link",
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        forceLink<any, any>(edges as any)
          .id((d: SimNode) => d.id)
          .distance((e: SimEdge) => {
            const src = (e.source as SimNode).gap;
            // Larger sibling spread: emergent children need room for their
            // labels not to overlap. Preset → root distance stays moderate.
            return src?.preset_kind ? 160 : 130;
          })
          .strength(0.3),
      )
      // Stronger repulsion so siblings don't clump. distanceMax wider so
      // the force reaches the full subtree, not just nearest neighbors.
      .force("charge", forceManyBody().strength(-340).distanceMax(600))
      // Center the emergent cluster in the upper-middle of the canvas, just
      // below the preset row.
      .force("center", forceCenter(w / 2, emergentCenterY).strength(0.06))
      // Soft pull toward the emergent center on the Y axis. Less aggressive
      // than before so the cluster can spread out a bit vertically.
      .force("y-anchor", forceY(emergentCenterY).strength(0.025))
      .alphaDecay(0.02)
      .velocityDecay(0.35);

    sim.alpha(0.6).restart();
    sedimentCount = buried.length;
  }

  let sedimentCount = 0;

  // ---- Render loop -------------------------------------------------------

  let rafId = 0;
  function render(t: number): void {
    if (!canvasRef) return;
    const dpr = window.devicePixelRatio || 1;
    const [w, h] = size();
    const ctx = canvasRef.getContext("2d");
    if (!ctx) return;
    canvasRef.width = w * dpr;
    canvasRef.height = h * dpr;
    canvasRef.style.width = `${w}px`;
    canvasRef.style.height = `${h}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const tr = transform();
    ctx.save();
    ctx.translate(tr.x, tr.y);
    ctx.scale(tr.k, tr.k);

    drawEdges(ctx);
    drawNodes(ctx, t);
    drawSediment(ctx, w, h);
    drawDrones(ctx, t);

    ctx.restore();
    rafId = requestAnimationFrame(render);
  }

  function drawEdges(ctx: CanvasRenderingContext2D): void {
    ctx.lineWidth = 0.6;
    for (const e of edges) {
      const src = typeof e.source === "string" ? null : e.source;
      const tgt = typeof e.target === "string" ? null : e.target;
      if (!src || !tgt) continue;
      const a = pickAuthorAccent(tgt.gap.status);
      const grad = ctx.createLinearGradient(src.x, src.y, tgt.x, tgt.y);
      grad.addColorStop(0, "rgba(60, 110, 245, 0.18)");
      grad.addColorStop(1, a + "55");
      ctx.strokeStyle = grad;
      ctx.beginPath();
      ctx.moveTo(src.x, src.y);
      ctx.lineTo(tgt.x, tgt.y);
      ctx.stroke();
    }
  }

  function drawNodes(ctx: CanvasRenderingContext2D, t: number): void {
    const beat = 0.5 + 0.5 * Math.sin((t / HEARTBEAT_MS) * Math.PI * 2);
    const selected = store.selected_gap_id;
    const pulseGap = store.alignment_pulse_gap_id;
    const findingsByGap = new Map<string, number>();
    for (const f of store.recent_findings) {
      for (const gid of f.affected_gap_ids) {
        findingsByGap.set(gid, (findingsByGap.get(gid) ?? 0) + 1);
      }
    }
    const activeGapIds = new Set(store.active_drones.map((d) => d.gap_id));

    for (const n of nodes) {
      const g = n.gap;
      const findingCount = findingsByGap.get(g.id) ?? 0;
      const baseR = g.preset_kind ? 18 : 12 + Math.min(14, Math.log2(1 + findingCount) * 4);
      const isActive = activeGapIds.has(g.id);
      const activityPulse = isActive ? 1 + 0.25 * beat : 1 + 0.04 * (beat - 0.5);
      const r = baseR * activityPulse;
      const accent = pickAuthorAccent(g.status);

      // Outer glow.
      const glowR = r * 3.2;
      const glowGrad = ctx.createRadialGradient(n.x, n.y, r * 0.6, n.x, n.y, glowR);
      glowGrad.addColorStop(0, accent + (isActive ? "80" : "40"));
      glowGrad.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = glowGrad;
      ctx.beginPath();
      ctx.arc(n.x, n.y, glowR, 0, Math.PI * 2);
      ctx.fill();

      // Core.
      const coreGrad = ctx.createRadialGradient(
        n.x - r * 0.3,
        n.y - r * 0.3,
        r * 0.1,
        n.x,
        n.y,
        r,
      );
      coreGrad.addColorStop(0, lighten(accent));
      coreGrad.addColorStop(0.55, accent);
      coreGrad.addColorStop(1, "rgba(0,0,0,0.6)");
      ctx.fillStyle = coreGrad;
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fill();

      // Preset ring outline.
      if (g.preset_kind) {
        ctx.strokeStyle = accent + "cc";
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 4, 0, Math.PI * 2);
        ctx.stroke();
        ctx.fillStyle = "var(--fg-1)";
      }

      // Alignment pulse.
      if (pulseGap === g.id) {
        ctx.strokeStyle = "rgba(245, 181, 60, 0.85)";
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 8 + beat * 6, 0, Math.PI * 2);
        ctx.stroke();
      }

      // Selection halo.
      if (selected === g.id || hoverGapId() === g.id) {
        ctx.strokeStyle = "rgba(227, 232, 241, 0.55)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 5, 0, Math.PI * 2);
        ctx.stroke();
      }

      // Label — three tiers of prominence:
      //  - presets: always shown, dim, uppercase kind name (e.g. GAP_FINDING)
      //  - selected/hovered: full intent text, brighter
      //  - everything else: short tag (first ~14 chars of intent), very dim,
      //    only when zoomed in enough to read it without colliding
      if (transform().k > 0.6) {
        const isFocused = selected === g.id || hoverGapId() === g.id;
        if (g.preset_kind) {
          ctx.font = `10px "JetBrains Mono", monospace`;
          ctx.fillStyle = "rgba(154, 164, 184, 0.85)";
          ctx.textAlign = "center";
          ctx.textBaseline = "top";
          ctx.fillText(g.preset_kind.toUpperCase(), n.x, n.y + r + 6);
        } else if (isFocused) {
          // Full intent above the node so it doesn't collide with siblings'
          // labels (which appear below).
          const label = oneLine(g.intent);
          ctx.font = `11px "JetBrains Mono", monospace`;
          ctx.fillStyle = "rgba(227, 232, 241, 0.95)";
          ctx.textAlign = "center";
          ctx.textBaseline = "bottom";
          const t = truncate(label, 60);
          // Subtle backdrop so the label reads cleanly on the canvas grain
          // even when crossing other nodes / edges.
          const m = ctx.measureText(t);
          const padX = 6;
          const padY = 3;
          ctx.fillStyle = "rgba(7, 10, 15, 0.88)";
          ctx.fillRect(
            n.x - m.width / 2 - padX,
            n.y - r - 8 - 12 - padY,
            m.width + padX * 2,
            12 + padY * 2,
          );
          ctx.fillStyle = "rgba(227, 232, 241, 0.95)";
          ctx.fillText(t, n.x, n.y - r - 8);
          if (g.status === "retired") {
            ctx.strokeStyle = "rgba(154, 164, 184, 0.6)";
            ctx.lineWidth = 0.7;
            ctx.beginPath();
            ctx.moveTo(n.x - m.width / 2, n.y - r - 8 - 5);
            ctx.lineTo(n.x + m.width / 2, n.y - r - 8 - 5);
            ctx.stroke();
          }
        } else if (transform().k > 0.85) {
          // Tiny tag for everyone else, only when zoomed in. Keeps the
          // canvas legible at default zoom but lets the user dig in.
          const label = oneLine(g.intent);
          ctx.font = `10px "JetBrains Mono", monospace`;
          ctx.fillStyle = "rgba(154, 164, 184, 0.55)";
          ctx.textAlign = "center";
          ctx.textBaseline = "top";
          ctx.fillText(truncate(label, 18), n.x, n.y + r + 6);
        }
      }
    }
  }

  function drawSediment(
    ctx: CanvasRenderingContext2D,
    w: number,
    h: number,
  ): void {
    if (sedimentCount === 0) return;
    const tr = transform();
    // Render in screen space so the band is always anchored to the bottom.
    ctx.save();
    ctx.setTransform(window.devicePixelRatio || 1, 0, 0, window.devicePixelRatio || 1, 0, 0);
    const bandY = h - 24;
    ctx.fillStyle = "rgba(42, 47, 56, 0.45)";
    ctx.fillRect(0, bandY, w, 24);
    ctx.fillStyle = "rgba(154, 164, 184, 0.6)";
    ctx.font = '10px "JetBrains Mono", monospace';
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(
      `sediment · ${sedimentCount} retired · click to ${
        sedimentExpanded ? "collapse" : "expand"
      }`,
      14,
      bandY + 12,
    );
    // Faint dots.
    for (let i = 0; i < Math.min(120, sedimentCount); i++) {
      const x = 220 + (i * 7) % (w - 240);
      const y = bandY + 12 + ((i * 13) % 4) - 2;
      ctx.fillStyle = "rgba(154, 164, 184, 0.35)";
      ctx.beginPath();
      ctx.arc(x, y, 1.5, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
    // Avoid unused-variable lint
    void tr;
  }

  function drawDrones(ctx: CanvasRenderingContext2D, t: number): void {
    const beat = 0.5 + 0.5 * Math.sin((t / HEARTBEAT_MS) * Math.PI * 2);
    const byGap = new Map<string, ActiveDrone[]>();
    for (const d of store.active_drones) {
      const arr = byGap.get(d.gap_id) ?? [];
      arr.push(d);
      byGap.set(d.gap_id, arr);
    }
    for (const n of nodes) {
      const drones = byGap.get(n.gap.id);
      if (!drones || drones.length === 0) continue;
      drones.forEach((d, i) => {
        const phase = (t / 2000 + i * 0.3) % 1;
        const orbit = 18 + (n.gap.preset_kind ? 6 : 4);
        const dx = n.x + Math.cos(phase * Math.PI * 2) * orbit;
        const dy = n.y + Math.sin(phase * Math.PI * 2) * orbit;
        const color = droneColor(d);
        // Trailing comet
        for (let j = 0; j < 6; j++) {
          const back = (phase - j * 0.04 + 1) % 1;
          const bx = n.x + Math.cos(back * Math.PI * 2) * orbit;
          const by = n.y + Math.sin(back * Math.PI * 2) * orbit;
          ctx.fillStyle = color + Math.floor((0.6 - j * 0.09) * 255).toString(16).padStart(2, "0");
          ctx.beginPath();
          ctx.arc(bx, by, 1.8 + (5 - j) * 0.3, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.fillStyle = color;
        ctx.shadowColor = color;
        ctx.shadowBlur = 12 + beat * 6;
        ctx.beginPath();
        ctx.arc(dx, dy, 3.2, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;
      });
    }
  }

  // ---- Mouse / input ----------------------------------------------------

  function clientToWorld(cx: number, cy: number): [number, number] {
    const rect = canvasRef!.getBoundingClientRect();
    const tr = transform();
    const x = (cx - rect.left - tr.x) / tr.k;
    const y = (cy - rect.top - tr.y) / tr.k;
    return [x, y];
  }

  function findNodeAt(cx: number, cy: number): SimNode | null {
    const [x, y] = clientToWorld(cx, cy);
    let best: SimNode | null = null;
    let bestD = 24;
    for (const n of nodes) {
      const dx = n.x - x;
      const dy = n.y - y;
      const d = Math.hypot(dx, dy);
      if (d < bestD) {
        bestD = d;
        best = n;
      }
    }
    return best;
  }

  function onMouseMove(e: MouseEvent): void {
    const n = findNodeAt(e.clientX, e.clientY);
    setHoverGapId(n?.id ?? null);
    canvasRef!.style.cursor = n ? "pointer" : panState.dragging ? "grabbing" : "grab";
    if (panState.dragging) {
      const tr = transform();
      setTransform({
        k: tr.k,
        x: tr.x + (e.clientX - panState.lastX),
        y: tr.y + (e.clientY - panState.lastY),
      });
      panState.lastX = e.clientX;
      panState.lastY = e.clientY;
    }
  }

  function onMouseDown(e: MouseEvent): void {
    if (e.button !== 0) return;
    // Always record where the click began so onMouseUp's distance check
    // (click vs drag) is accurate — even for clicks that land on a node.
    // Previously we only updated these on background clicks, and mouseup
    // would compute distance against a stale value from the prior drag,
    // mis-classifying node clicks as drags and silently dropping them.
    panState.startX = e.clientX;
    panState.startY = e.clientY;
    panState.lastX = e.clientX;
    panState.lastY = e.clientY;
    const n = findNodeAt(e.clientX, e.clientY);
    if (n) return; // node click handled in onMouseUp
    panState.dragging = true;
    // Attach mouseup to window so releasing outside the canvas still ends
    // the drag. Without this, fast drags can leave the canvas in pan mode.
    window.addEventListener("mouseup", onWindowMouseUp, { once: true });
  }

  function onWindowMouseUp(e: MouseEvent): void {
    onMouseUp(e);
  }

  function onMouseUp(e: MouseEvent): void {
    const moved =
      Math.abs(e.clientX - panState.startX) + Math.abs(e.clientY - panState.startY);
    panState.dragging = false;
    if (moved > 4) return;
    // Only treat the release as a node-click if it happened over the canvas.
    if (!canvasRef) return;
    const rect = canvasRef.getBoundingClientRect();
    const overCanvas =
      e.clientX >= rect.left &&
      e.clientX <= rect.right &&
      e.clientY >= rect.top &&
      e.clientY <= rect.bottom;
    if (!overCanvas) return;
    const n = findNodeAt(e.clientX, e.clientY);
    if (n) {
      selectGap(n.id);
    } else {
      const yIn = e.clientY - rect.top;
      if (yIn > rect.height - 28 && sedimentCount > 0) {
        sedimentExpanded = !sedimentExpanded;
        buildSim();
      }
    }
  }

  function onContextMenu(e: MouseEvent): void {
    e.preventDefault();
    const n = findNodeAt(e.clientX, e.clientY);
    if (!n) return;
    setMenu({ x: e.clientX, y: e.clientY, gap: n.gap });
  }

  function onDoubleClick(e: MouseEvent): void {
    const n = findNodeAt(e.clientX, e.clientY);
    if (!n) return;
    // Focus active drone on this gap, if any.
    const d = store.active_drones.find((dd) => dd.gap_id === n.id);
    if (d) focusDrone(n.id);
  }

  function onWheel(e: WheelEvent): void {
    e.preventDefault();
    const tr = transform();
    const dk = Math.exp(-e.deltaY * 0.0015);
    const rect = canvasRef!.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const newK = Math.max(0.3, Math.min(2.5, tr.k * dk));
    // Zoom around mouse.
    const nx = mx - (mx - tr.x) * (newK / tr.k);
    const ny = my - (my - tr.y) * (newK / tr.k);
    setTransform({ k: newK, x: nx, y: ny });
  }

  function onKeyDown(e: KeyboardEvent): void {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
    if (e.key === "f" || e.key === "F") {
      const [w, h] = size();
      setTransform({ k: 1, x: 0, y: 0 });
      void w; void h;
    } else if (e.key === "Escape") {
      selectGap(null);
      setMenu(null);
    }
  }

  const panState = {
    dragging: false,
    startX: 0,
    startY: 0,
    lastX: 0,
    lastY: 0,
  };

  // ---- Mount / lifecycle ------------------------------------------------

  onMount(() => {
    const ro = new ResizeObserver(() => {
      if (containerRef) {
        const r = containerRef.getBoundingClientRect();
        setSize([Math.max(200, r.width), Math.max(200, r.height)]);
      }
    });
    if (containerRef) ro.observe(containerRef);
    buildSim();
    rafId = requestAnimationFrame(render);
    window.addEventListener("keydown", onKeyDown);
    onCleanup(() => {
      cancelAnimationFrame(rafId);
      ro.disconnect();
      if (sim) sim.stop();
      window.removeEventListener("keydown", onKeyDown);
    });
  });

  // Rebuild simulation when the graph shape meaningfully changes.
  createEffect(() => {
    void store.gaps.length;
    void store.parent_edges.length;
    buildSim();
  });

  // Show last finding briefly when one arrives.
  createEffect(() => {
    const f = store.recent_findings[store.recent_findings.length - 1];
    if (f) {
      setRecentFinding(f);
      const id = window.setTimeout(() => setRecentFinding(null), 4500);
      onCleanup(() => window.clearTimeout(id));
    }
  });

  return (
    <div class="canvas-wrap" ref={containerRef}>
      <canvas
        ref={canvasRef}
        onMouseMove={onMouseMove}
        onMouseDown={onMouseDown}
        onMouseUp={onMouseUp}
        onContextMenu={onContextMenu}
        onDblClick={onDoubleClick}
        onWheel={onWheel}
      />
      <div class="hud">
        <div class="hud-row">
          <span class="tag graphite">
            {store.gaps.filter((g) => !g.preset_kind).length} gaps · {store.parent_edges.length} edges
          </span>
          <span class="tag graphite">
            {store.active_drones.length} drones live
          </span>
        </div>
        {recentFinding() && (
          <div class="finding-ticker">
            <FindingTicker f={recentFinding()!} />
          </div>
        )}
      </div>
      {menu() && (
        <ContextMenu
          x={menu()!.x}
          y={menu()!.y}
          gap={menu()!.gap!}
          onClose={() => setMenu(null)}
        />
      )}
      <style>{CSS}</style>
    </div>
  );
}

function FindingTicker(props: { f: Finding }) {
  return (
    <span>
      <span
        class={`tag ${authorTag(props.f.author)}`}
        style={{ "margin-right": "8px" }}
      >
        {props.f.author}
      </span>
      <span class="dim">{props.f.kind}</span>
      <span style={{ "margin-left": "10px" }}>{oneLine(props.f.summary)}</span>
    </span>
  );
}

// ---- Helpers ---------------------------------------------------------------

function authorTag(a: Finding["author"]): string {
  return {
    user: "cobalt",
    gap_finding: "cobalt",
    alignment: "amber",
    worker: "teal",
    system: "graphite",
  }[a];
}

function pickAuthorAccent(status: Gap["status"]): string {
  return {
    unfilled: "#3c6ef5", // cobalt
    filled: "#7fe5d0", // teal
    retired: "#2a2f38", // graphite
  }[status];
}

function droneColor(d: ActiveDrone): string {
  if (d.role === "preset:gap_finding") return "#b48cff";
  if (d.role === "preset:alignment") return "#f5b53c";
  return "#6fd0a8";
}

function lighten(hex: string): string {
  return hex + "ee";
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

function oneLine(s: string): string {
  const i = s.indexOf("\n");
  return (i === -1 ? s : s.slice(0, i)).trim();
}

const CSS = `
.canvas-wrap {
  position: relative;
  flex: 1;
  min-height: 0;
  background: var(--bg-0);
  overflow: hidden;
}
.canvas-wrap canvas {
  display: block;
  cursor: grab;
  user-select: none;
}

.hud {
  position: absolute;
  inset: 12px 12px auto 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  pointer-events: none;
}
.hud-row {
  display: flex;
  gap: 8px;
}

.finding-ticker {
  background: rgba(11, 15, 23, 0.85);
  border: 1px solid var(--border);
  padding: 6px 10px;
  font-size: var(--fs-sm);
  letter-spacing: 0.01em;
  max-width: 720px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  pointer-events: auto;
}
`;
