import {
  For,
  Show,
  createEffect,
  createMemo,
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

import { selectFinding, selectGap, store } from "../state";
import type { Finding, FindingAuthor, Gap } from "../types";

/** Findings ledger visualised as a graph. Gaps are the primary nodes
 *  (force-laid-out like on the console canvas). Each gap's findings orbit
 *  it as smaller satellite dots, coloured by author, kind tag floating
 *  beside on hover.
 *
 *  Click a satellite → side panel opens with the full finding text and
 *  artefact paths. Filter bar at the top lets the operator narrow by
 *  author or kind.
 */

interface SimNode {
  id: string;
  gap: Gap;
  x: number;
  y: number;
  vx: number;
  vy: number;
  fx?: number | null;
  fy?: number | null;
  r: number;
}

interface SimEdge {
  source: SimNode | string;
  target: SimNode | string;
}

interface SatellitePos {
  finding: Finding;
  x: number;
  y: number;
  r: number;
}

const HEARTBEAT_MS = 800;
const MAX_SATELLITES_PER_GAP = 24; // cap to keep dense gaps readable
const SATELLITE_RING_PADDING = 22;
const SATELLITE_RING_STRIDE = 16;

export function FindingsGraph() {
  let canvasRef: HTMLCanvasElement | undefined;
  let containerRef: HTMLDivElement | undefined;
  const [size, setSize] = createSignal<[number, number]>([800, 600]);
  const [transform, setTransform] = createSignal<{
    k: number;
    x: number;
    y: number;
  }>({ k: 1, x: 0, y: 0 });
  const [hoverFindingId, setHoverFindingId] = createSignal<string | null>(null);
  const [hoverGapId, setHoverGapId] = createSignal<string | null>(null);
  const [authorFilter, setAuthorFilter] = createSignal<FindingAuthor | "all">(
    "all",
  );
  const [kindFilter, setKindFilter] = createSignal<string>("");

  let nodes: SimNode[] = [];
  let edges: SimEdge[] = [];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let sim: any = null;
  let satellites: SatellitePos[] = [];

  const filteredFindings = createMemo<Finding[]>(() => {
    const a = authorFilter();
    const k = kindFilter().trim().toLowerCase();
    return store.recent_findings.filter((f) => {
      if (a !== "all" && f.author !== a) return false;
      if (k && !f.kind.toLowerCase().includes(k)) return false;
      return true;
    });
  });

  const selectedFinding = createMemo<Finding | null>(() => {
    const id = store.selected_finding_id;
    if (!id) return null;
    return store.recent_findings.find((f) => f.id === id) ?? null;
  });

  // ---- Build gap layout (mirrors SubstrateCanvas, simpler) -------------

  function buildSim(): void {
    const [w, h] = size();
    const visible = store.gaps.filter((g) => g.status !== "retired");

    const presetY = Math.max(60, h * 0.12);
    const emergentCenterY = Math.min(h * 0.55, presetY + 220);

    const prev = new Map(nodes.map((n) => [n.id, n]));
    nodes = visible.map((g, i) => {
      const p = prev.get(g.id);
      const angle = (i / Math.max(1, visible.length)) * Math.PI * 2;
      const r = 18 + Math.min(14, Math.log2(1 + countFor(g.id)) * 3);
      return {
        id: g.id,
        gap: g,
        x: p?.x ?? w / 2 + Math.cos(angle) * 120,
        y: p?.y ?? emergentCenterY + Math.sin(angle) * 120,
        vx: p?.vx ?? 0,
        vy: p?.vy ?? 0,
        fx: null,
        fy: null,
        r,
      };
    });

    // Pin presets to upper band.
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
            return src?.preset_kind ? 160 : 140;
          })
          .strength(0.3),
      )
      .force("charge", forceManyBody().strength(-380).distanceMax(700))
      .force("center", forceCenter(w / 2, emergentCenterY).strength(0.06))
      .force("y-anchor", forceY(emergentCenterY).strength(0.025))
      .alphaDecay(0.02)
      .velocityDecay(0.35);

    sim.alpha(0.6).restart();
  }

  function countFor(gapId: string): number {
    let n = 0;
    for (const f of filteredFindings()) {
      if (f.affected_gap_ids.includes(gapId)) n++;
    }
    return n;
  }

  // ---- Satellite layout per frame ------------------------------------

  function computeSatellites(): void {
    const findingsByGap = new Map<string, Finding[]>();
    // Group by primary affected gap (first in affected_gap_ids), oldest
    // first so newer findings end up at the outer ring with more space.
    for (const f of filteredFindings()) {
      const gid = f.affected_gap_ids[0];
      if (!gid) continue;
      const arr = findingsByGap.get(gid) ?? [];
      arr.push(f);
      findingsByGap.set(gid, arr);
    }
    const out: SatellitePos[] = [];
    for (const n of nodes) {
      const list = findingsByGap.get(n.gap.id);
      if (!list || list.length === 0) continue;
      const recent = list.slice(-MAX_SATELLITES_PER_GAP);
      // Multi-ring arrangement when count > 12 — distribute across
      // concentric rings so ring isn't crowded.
      const total = recent.length;
      const perRing = 12;
      const numRings = Math.ceil(total / perRing);
      let idx = 0;
      for (let ringI = 0; ringI < numRings; ringI++) {
        const inRing = Math.min(perRing, total - ringI * perRing);
        const ringR = n.r + SATELLITE_RING_PADDING + ringI * SATELLITE_RING_STRIDE;
        for (let i = 0; i < inRing; i++) {
          const angle = (i / inRing) * Math.PI * 2 - Math.PI / 2;
          out.push({
            finding: recent[idx],
            x: n.x + Math.cos(angle) * ringR,
            y: n.y + Math.sin(angle) * ringR,
            r: 4,
          });
          idx++;
        }
      }
    }
    satellites = out;
  }

  // ---- Render loop -----------------------------------------------------

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
    drawGaps(ctx, t);
    computeSatellites();
    drawSatellites(ctx, t);

    ctx.restore();
    rafId = requestAnimationFrame(render);
  }

  function drawEdges(ctx: CanvasRenderingContext2D): void {
    ctx.lineWidth = 0.6;
    ctx.strokeStyle = "rgba(60, 110, 245, 0.16)";
    for (const e of edges) {
      const src = typeof e.source === "string" ? null : e.source;
      const tgt = typeof e.target === "string" ? null : e.target;
      if (!src || !tgt) continue;
      ctx.beginPath();
      ctx.moveTo(src.x, src.y);
      ctx.lineTo(tgt.x, tgt.y);
      ctx.stroke();
    }
  }

  function drawGaps(ctx: CanvasRenderingContext2D, t: number): void {
    const beat = 0.5 + 0.5 * Math.sin((t / HEARTBEAT_MS) * Math.PI * 2);
    for (const n of nodes) {
      const g = n.gap;
      const accent = pickAuthorAccent(g.status);
      const r = n.r;
      // Glow.
      const glow = ctx.createRadialGradient(n.x, n.y, r * 0.6, n.x, n.y, r * 3);
      glow.addColorStop(0, accent + "55");
      glow.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(n.x, n.y, r * 3, 0, Math.PI * 2);
      ctx.fill();
      // Core.
      const core = ctx.createRadialGradient(
        n.x - r * 0.3,
        n.y - r * 0.3,
        r * 0.1,
        n.x,
        n.y,
        r,
      );
      core.addColorStop(0, lighten(accent));
      core.addColorStop(0.55, accent);
      core.addColorStop(1, "rgba(0,0,0,0.6)");
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fill();
      // Preset ring.
      if (g.preset_kind) {
        ctx.strokeStyle = accent + "cc";
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 4, 0, Math.PI * 2);
        ctx.stroke();
      }
      // Selection / hover halo.
      if (hoverGapId() === g.id || store.selected_gap_id === g.id) {
        ctx.strokeStyle = "rgba(227, 232, 241, 0.55)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 6, 0, Math.PI * 2);
        ctx.stroke();
      }
      // Label.
      if (transform().k > 0.55) {
        const label = g.preset_kind
          ? g.preset_kind.toUpperCase()
          : truncate(oneLine(g.intent), 22);
        ctx.font = `10px "JetBrains Mono", monospace`;
        ctx.fillStyle = g.preset_kind
          ? "rgba(154, 164, 184, 0.85)"
          : "rgba(154, 164, 184, 0.6)";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        // Place gap label BELOW the outermost satellite ring so it doesn't
        // collide with satellites. Approx max ring radius:
        const labelY =
          n.y + r + SATELLITE_RING_PADDING + SATELLITE_RING_STRIDE * 2 + 10;
        ctx.fillText(label, n.x, labelY);
      }
      void beat;
    }
  }

  function drawSatellites(ctx: CanvasRenderingContext2D, t: number): void {
    const beat = 0.5 + 0.5 * Math.sin((t / HEARTBEAT_MS) * Math.PI * 2);
    const selected = store.selected_finding_id;
    const hovered = hoverFindingId();
    for (const s of satellites) {
      const color = authorColor(s.finding.author);
      const isFocused = selected === s.finding.id || hovered === s.finding.id;
      const r = s.r * (isFocused ? 1.7 : 1);

      // Glow when focused.
      if (isFocused) {
        ctx.fillStyle = color + "55";
        ctx.beginPath();
        ctx.arc(s.x, s.y, r * 2.4, 0, Math.PI * 2);
        ctx.fill();
      }

      ctx.fillStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = isFocused ? 12 : 6;
      ctx.beginPath();
      ctx.arc(s.x, s.y, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;

      // Outline by kind family.
      const ring = kindRingColor(s.finding.kind);
      if (ring) {
        ctx.strokeStyle = ring;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(s.x, s.y, r + 1.6, 0, Math.PI * 2);
        ctx.stroke();
      }
      void beat;
    }
  }

  // ---- Mouse handling --------------------------------------------------

  function clientToWorld(cx: number, cy: number): [number, number] {
    const rect = canvasRef!.getBoundingClientRect();
    const tr = transform();
    return [
      (cx - rect.left - tr.x) / tr.k,
      (cy - rect.top - tr.y) / tr.k,
    ];
  }

  function findSatelliteAt(cx: number, cy: number): SatellitePos | null {
    const [x, y] = clientToWorld(cx, cy);
    let best: SatellitePos | null = null;
    let bestD = 10;
    for (const s of satellites) {
      const d = Math.hypot(s.x - x, s.y - y);
      if (d < bestD) {
        bestD = d;
        best = s;
      }
    }
    return best;
  }

  function findGapAt(cx: number, cy: number): SimNode | null {
    const [x, y] = clientToWorld(cx, cy);
    let best: SimNode | null = null;
    let bestD = 22;
    for (const n of nodes) {
      const d = Math.hypot(n.x - x, n.y - y);
      if (d < bestD) {
        bestD = d;
        best = n;
      }
    }
    return best;
  }

  const panState = {
    dragging: false,
    startX: 0,
    startY: 0,
    lastX: 0,
    lastY: 0,
  };

  function onMouseMove(e: MouseEvent): void {
    const sat = findSatelliteAt(e.clientX, e.clientY);
    setHoverFindingId(sat ? sat.finding.id : null);
    if (!sat) {
      const g = findGapAt(e.clientX, e.clientY);
      setHoverGapId(g ? g.id : null);
    } else {
      setHoverGapId(null);
    }
    canvasRef!.style.cursor =
      sat || findGapAt(e.clientX, e.clientY)
        ? "pointer"
        : panState.dragging
        ? "grabbing"
        : "grab";
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
    panState.startX = e.clientX;
    panState.startY = e.clientY;
    panState.lastX = e.clientX;
    panState.lastY = e.clientY;
    if (findSatelliteAt(e.clientX, e.clientY) || findGapAt(e.clientX, e.clientY)) {
      return; // clicks handled on mouseup
    }
    panState.dragging = true;
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
    const sat = findSatelliteAt(e.clientX, e.clientY);
    if (sat) {
      selectFinding(sat.finding.id);
      return;
    }
    const g = findGapAt(e.clientX, e.clientY);
    if (g) {
      selectGap(g.id);
      selectFinding(null);
    }
  }

  function onWheel(e: WheelEvent): void {
    e.preventDefault();
    const tr = transform();
    const dk = Math.exp(-e.deltaY * 0.0015);
    const rect = canvasRef!.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const newK = Math.max(0.3, Math.min(2.5, tr.k * dk));
    const nx = mx - (mx - tr.x) * (newK / tr.k);
    const ny = my - (my - tr.y) * (newK / tr.k);
    setTransform({ k: newK, x: nx, y: ny });
  }

  function onKeyDown(e: KeyboardEvent): void {
    if (
      e.target instanceof HTMLInputElement ||
      e.target instanceof HTMLTextAreaElement
    )
      return;
    if (e.key === "Escape") {
      selectFinding(null);
      selectGap(null);
    }
  }

  // ---- Lifecycle ----------------------------------------------------

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

  createEffect(() => {
    void store.gaps.length;
    void store.parent_edges.length;
    buildSim();
  });

  // ---- UI -----------------------------------------------------------

  const allKinds = createMemo<string[]>(() => {
    const set = new Set<string>();
    for (const f of store.recent_findings) set.add(f.kind);
    return [...set].sort();
  });

  return (
    <div class="findings-view">
      <div class="filters">
        <span class="dim small">filter</span>
        <select
          value={authorFilter()}
          onChange={(e) =>
            setAuthorFilter(e.currentTarget.value as FindingAuthor | "all")
          }
        >
          <option value="all">all authors</option>
          <option value="gap_finding">gap_finding</option>
          <option value="alignment">alignment</option>
          <option value="worker">worker</option>
          <option value="user">user</option>
          <option value="system">system</option>
        </select>
        <select
          value={kindFilter()}
          onChange={(e) => setKindFilter(e.currentTarget.value)}
        >
          <option value="">all kinds</option>
          <For each={allKinds()}>{(k) => <option value={k}>{k}</option>}</For>
        </select>
        <span class="dim small spacer">
          {filteredFindings().length} of {store.recent_findings.length} findings
        </span>
        <div class="legend">
          <Legend />
        </div>
      </div>
      <div class="graph-wrap" ref={containerRef}>
        <canvas
          ref={canvasRef}
          onMouseMove={onMouseMove}
          onMouseDown={onMouseDown}
          onMouseUp={onMouseUp}
          onWheel={onWheel}
        />
      </div>
      <FindingDetail finding={selectedFinding()} />
      <style>{CSS}</style>
    </div>
  );
}

function Legend() {
  const items: { color: string; label: string }[] = [
    { color: "#3c6ef5", label: "user / GF" },
    { color: "#b48cff", label: "GF (alt)" },
    { color: "#f5b53c", label: "alignment" },
    { color: "#7fe5d0", label: "worker" },
    { color: "#687589", label: "system" },
  ];
  return (
    <For each={items}>
      {(i) => (
        <span class="legend-item">
          <span class="dot" style={{ background: i.color }} />
          <span class="dim small">{i.label}</span>
        </span>
      )}
    </For>
  );
}

function FindingDetail(props: { finding: Finding | null }) {
  return (
    <Show when={props.finding}>
      <div class="finding-detail">
        <div class="fd-head">
          <span class={`tag ${authorTagClass(props.finding!.author)}`}>
            {props.finding!.author}
          </span>
          <span class="tag graphite">{props.finding!.kind}</span>
          <span class="dim mono small">tick {props.finding!.tick}</span>
          <span style={{ flex: "1" }} />
          <button class="ghost" onClick={() => selectFinding(null)}>
            close
          </button>
        </div>
        <div class="fd-body">
          <pre class="fd-summary">{props.finding!.summary}</pre>
          <Show when={props.finding!.affected_gap_ids.length > 0}>
            <div class="fd-section">
              <div class="fd-label">AFFECTED GAPS</div>
              <div class="row" style={{ "flex-wrap": "wrap", gap: "4px" }}>
                <For each={props.finding!.affected_gap_ids}>
                  {(gid) => (
                    <a
                      class="tag cobalt clickable"
                      onClick={() => selectGap(gid)}
                    >
                      {gid.slice(0, 8)}
                    </a>
                  )}
                </For>
              </div>
            </div>
          </Show>
          <Show when={props.finding!.artefact_paths.length > 0}>
            <div class="fd-section">
              <div class="fd-label">ARTEFACTS</div>
              <For each={props.finding!.artefact_paths}>
                {(p) => <div class="fd-artefact mono small">{p}</div>}
              </For>
            </div>
          </Show>
          <Show when={props.finding!.invocation_tool_name}>
            <div class="fd-section">
              <div class="fd-label">INVOCATION</div>
              <div class="small">
                tool <span class="mono">{props.finding!.invocation_tool_name}</span>{" "}
                · outcome {props.finding!.invocation_outcome ?? "—"}
                <Show when={props.finding!.invocation_cost_usd}>
                  · ${props.finding!.invocation_cost_usd!.toFixed(4)}
                </Show>
              </div>
            </div>
          </Show>
        </div>
      </div>
    </Show>
  );
}

// ---- Color helpers ----------------------------------------------------

function pickAuthorAccent(status: Gap["status"]): string {
  return { unfilled: "#3c6ef5", filled: "#7fe5d0", retired: "#2a2f38" }[status];
}

function authorColor(a: FindingAuthor): string {
  return {
    user: "#3c6ef5",
    gap_finding: "#b48cff",
    alignment: "#f5b53c",
    worker: "#7fe5d0",
    system: "#687589",
  }[a];
}

function kindRingColor(kind: string): string | null {
  // Outline ring distinguishes families: structural (decompose/create/retire/
  // reopen/rewrite) get a bluish ring; outcomes (fill/fail/requires_user_action)
  // get a teal/copper; alignment kinds inherit author color; notes are blank.
  if (
    kind === "decompose" ||
    kind === "create" ||
    kind === "retire" ||
    kind === "reopen" ||
    kind === "rewrite_intent"
  ) {
    return "rgba(60, 110, 245, 0.45)";
  }
  if (kind === "fail" || kind === "requires_user_action") {
    return "rgba(226, 107, 67, 0.55)";
  }
  if (kind === "fill") {
    return "rgba(127, 229, 208, 0.55)";
  }
  return null;
}

function authorTagClass(a: FindingAuthor): string {
  return {
    user: "cobalt",
    gap_finding: "cobalt",
    alignment: "amber",
    worker: "teal",
    system: "graphite",
  }[a];
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
.findings-view {
  position: relative;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.findings-view .filters {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 18px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.findings-view select {
  background: var(--bg-2);
  color: var(--fg-0);
  border: 1px solid var(--border);
  padding: 3px 6px;
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
}
.findings-view .small { font-size: var(--fs-xs); }
.findings-view .spacer { margin-left: 12px; }
.findings-view .legend {
  margin-left: auto;
  display: flex;
  gap: 10px;
  align-items: center;
}
.findings-view .legend-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.findings-view .legend-item .dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
}
.findings-view .graph-wrap {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.findings-view canvas {
  display: block;
  cursor: grab;
  user-select: none;
}

.finding-detail {
  position: absolute;
  right: 0;
  top: 49px; /* below filters */
  bottom: 0;
  width: min(420px, 40%);
  background: var(--bg-1);
  border-left: 1px solid var(--border-strong);
  display: flex;
  flex-direction: column;
  z-index: 5;
  box-shadow: -8px 0 24px rgba(0, 0, 0, 0.5);
}
.fd-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-2);
}
.fd-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.fd-summary {
  margin: 0;
  white-space: pre-wrap;
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  line-height: 1.55;
  color: var(--fg-0);
}
.fd-section { display: flex; flex-direction: column; gap: 5px; }
.fd-label {
  font-size: var(--fs-xs);
  letter-spacing: 0.08em;
  color: var(--fg-2);
}
.fd-artefact {
  word-break: break-all;
  color: var(--fg-1);
}
.tag.clickable { cursor: pointer; }
.tag.clickable:hover { background: var(--bg-3); }
`;
