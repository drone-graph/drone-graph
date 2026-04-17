# Landing Page Brief — Drone Graph

This document briefs a coding agent to build the marketing landing page for **Drone Graph**, a protocol-governed, graph-native AI execution system built around disposable drones operating as a self-organizing swarm.

The brand name, copy, and visual direction below are committed. A few decorative slots are explicitly marked `[PLACEHOLDER]` and should be left visibly unfilled so they can be replaced later.

---

## 1. Brand

**Name:** Drone Graph
**Internal terms used throughout:** drones (execution units), the swarm (the collective of active drones), three graphs (Task / Findings / Knowledge), protocols, capability routing.

**Hero slogan (use as-is):**
> AI drones for civilization-scale work.

**Primary call to action (use as-is):**
> Kill the AI org chart.

**Secondary call to action (use as-is, in a different location):**
> Fire your fake AI CEO.

**Domain (assume):** dronegraph.dev — wire all CTAs to the root for now.

### Voice

Calm presentation of cosmic claims. Confident, terse, slightly dry — but with permission to make ambitious statements where they're earned. Reference voice: SQLite docs and Tailscale's homepage for the engineering sections; Anduril, SpaceX, and Anthropic's bolder essays for the headline tier. The contrast between disciplined typography and large claims is the brand signature.

Avoid: exclamation marks, emoji, "agentic," "unlock," "empower," "revolutionize," any phrase that sounds like a Series A fundraising deck.

### Visual identity

- **Palette:** primary background cream (#F5F1E8), primary type deep ink (#1A1A1A). One accent — **electric cobalt** (#1E3AE5) used sparingly for verbs, links, and one or two key highlights. A single inverse band uses **near-black** (#0A0A0A) background with cream type for the cosmic/swarm section. No gradients, no glassmorphism, no glow effects.
- **Typography:** serif workhorse for body and headlines (use **Source Serif 4**, fall back to Georgia). Clean monospace for code, captions, and metadata (use **JetBrains Mono**, fall back to ui-monospace). Sans only for small UI labels if necessary.
- **Type scale:** very large headlines (clamp 56–128px). Generous body (18–20px). Tight leading on display type, wide leading on body. The page should feel like a manifesto, not a SaaS site.
- **Layout:** single column, max-width ~720px for prose. Full-bleed visual sections break it up. Plenty of vertical space. No three-column "feature grids," no logo wall, no testimonial carousel.
- **Motion:** restrained. Section fade-ins on scroll. Nothing parallax. The hero illustration may have a slow, ambient pulse on the active drone nodes — subtle, almost imperceptible.
- **Imagery aesthetic:** technical network diagrams, rendered as ink illustration on cream — or chalk-style illustration on near-black for the inverse band. Many small nodes, dense filaments, swarm topology. No stock photography, no 3D renders, no AI-generated robot imagery.

---

## 2. Page structure

Build the page in this order. Each section gets its own block of vertical space.

1. Hero
2. The problem
3. The thesis (typographic centerpiece)
4. The swarm (full-bleed inverse band — the cosmic moment)
5. How it works (architecture)
6. The three graphs
7. A worked example
8. What it's for / what it isn't for
9. For founders / for researchers
10. Open source call-out
11. Footer

Detailed copy and visual notes for each section follow.

---

## 3. Hero

**Layout:** full viewport height. Slogan dominant, sub-line below, primary CTA, hero visual to the right (or behind on mobile).

**Slogan (render in serif at clamp(72px, 10vw, 144px), tight leading, deep ink on cream):**
> AI drones for civilization-scale work.

**Sub-line (use as-is):**
> Disposable drones do the work. Three graphs hold the memory. A self-organizing swarm replaces your fake AI org chart with something that actually finishes the job.

**Primary CTA button (electric cobalt fill, cream type):** `Kill the AI org chart` → anchors to section 2.
**Secondary CTA (text link, smaller, mono):** `View on GitHub` → href="#" placeholder.

**Hero visual:** `[PLACEHOLDER — HERO ILLUSTRATION]`
Description for the illustrator: a dense swarm of small nodes connected by fine ink filaments — roughly 80–150 nodes — arranged as a loose, organically-bounded cloud rather than a neat grid. A handful of nodes are filled solid (active drones); most are hollow rings (the persistent graph). Three or four are highlighted in electric cobalt. The overall impression should be of a coordinated swarm, not chaos. Render as SVG so it scales crisply, with optional very subtle ambient pulsing on the cobalt nodes. The illustration occupies roughly 45% of the hero area on desktop, drops below the type on mobile.

For now, render a placeholder `<div>` with a 1px dashed cobalt border, the text `[hero illustration: dense drone swarm, ink on cream, with a few cobalt-highlighted active drones]` centred inside in mono, aspect ratio 4:3.

---

## 4. The problem

**Section heading (large serif):**
> The org chart is the bug.

**Body (use as-is):**
> Every multi-agent framework shipped in the last two years has the same defect. They give AI agents job titles, personalities, and org charts. One is the CEO. One is Marketing. One is a researcher named Maya with opinions about Q4 strategy. Underneath the costumes, they're all the same model with slightly different system prompts. The org chart is theatre. Hierarchy is legacy software.
>
> The result: hallucinated completion, drift away from the original goal, runaway token cost, and chat histories pretending to be memory. The systems look great in demos and collapse in production.
>
> Drone Graph does none of that.

**Pull-quote at the foot of the section (mono, electric cobalt, smaller):**
> AI doesn't need middle management.

No visual in this section. Pure typography.

---

## 5. The thesis (typographic centerpiece)

The design law of the system, presented as the visual heart of the page.

**Layout:** centred, generous whitespace. Each line on its own row. Render in serif, large (clamp 36–60px). Verbs (`handle`) in electric cobalt; the rest in deep ink.

```
Protocols handle coordination.
LLMs handle semantic judgment.
Graphs handle memory.
Drones handle execution.
```

Below, in monospace caption (16px, ink at 70% opacity):
> Coordination without managers. Every layer earns its place. If a box exists because it looks sophisticated, we delete it.

---

## 6. The swarm (full-bleed inverse band)

This is the cosmic moment of the page — one section that breaks the cream/ink rhythm to make the ambition tier legible. Background: near-black (#0A0A0A). Type: cream. Optional: a faint chalk-style swarm illustration in the background at 15% opacity.

**Section heading (cream serif, very large — clamp 64–112px):**
> Don't build a fake company. Build a swarm.

**Body (cream serif body, generous leading):**
> A drone is not an employee. It has no résumé, no job title, no career, no ego. It is spawned for a specific task, given the tools, context, permissions, and budget it needs, allowed to do its work, and then terminated. The next task spawns the next drone. The swarm is not assembled — it emerges, mission by mission, from a shared substrate of protocols and graphs.
>
> Run your company like a species. The individuals are short-lived. The collective endures. The work organizes itself.

**Pull-quote at the foot (mono, cream at 80% opacity, smaller):**
> Mission control for the hivemind.

No CTA in this section — let the rhythm carry the reader through.

---

## 7. How it works (architecture)

Back to cream/ink. Engineering voice resumes.

**Section heading:**
> An execution substrate, not a workforce.

**Body (use as-is):**
> Drone Graph treats AI as what it actually is: a fast, fungible, semantically capable execution unit. Around the drones sits a thin set of protocols that handle everything deterministic — task admission, capability routing, claim-and-lease, retries, budgets, escalation. Language models are called only where semantic judgment is genuinely required: decomposing a fuzzy goal into atomic tasks, checking whether outputs satisfy intent, resolving ambiguous evidence. Coordination is not the LLM's job.

**Visual:** `[PLACEHOLDER — ARCHITECTURE DIAGRAM]`
Description: a clean architectural diagram showing the flow: User Goal → Decomposer → Task Graph → Admission → Capability Router → Drone(s) → Signal Layer → Protector → Gatekeeper → Findings Graph / Knowledge Graph, with the Aligner shown as a feedback loop back to Decomposer. Style: black ink on cream, hand-drawn feel, labelled in monospace, with electric cobalt arrows for the primary forward flow. Roughly 800px wide, 500px tall, full-bleed within the section.

For now, render a placeholder `<div>` with the same dashed cobalt border treatment and the text `[architecture diagram: data flow from user goal through decomposition, routing, drone execution, triage, and into the three graphs, with cobalt arrows on the primary path]`.

**Pull-quote below diagram (mono, electric cobalt, smaller):**
> Self-organizing AI for complex work.

---

## 8. The three graphs

The single most important technical idea, given dedicated space.

**Section heading:**
> Three graphs. Three truth standards.

**Body (use as-is):**
> Every other agent stack treats memory as a chat history with retrieval glue. Drone Graph separates memory into three distinct graph spaces, each with its own truth standard, lifetime, and access rules.

Render a three-column block (stacks on mobile). Each column has a small ink illustration (placeholder), a bold serif heading, and a short body paragraph in serif. Captions in mono.

**Column 1 — Task Graph**
> What is happening. Goals, atomic tasks, dependencies, ownership, retries, blockers. The execution structure of the mission. Lives only as long as the mission does.

Visual placeholder: `[ink sketch: a directed acyclic graph of small task nodes]`

**Column 2 — Findings Graph**
> What is being learned. Hypotheses, raw evidence, partial conclusions, weak signals, unresolved contradictions. Temporary by design. The Sleeper layer prunes stale findings; the Gatekeeper promotes verified ones.

Visual placeholder: `[ink sketch: a loose cluster of partially connected observation nodes, some fading]`

**Column 3 — Knowledge Graph**
> What is known. Verified facts, validated decisions, durable conclusions. Every entry carries source, timestamp, confidence, provenance chain, verification status. Long-term memory — but structured.

Visual placeholder: `[ink sketch: a denser, more orderly graph of validated nodes with provenance arrows]`

**Caption below the three columns (mono, small, full width):**
> Findings are not knowledge. Knowledge is not chat history. Treating them the same is how agent systems hallucinate themselves into incoherence.

---

## 9. A worked example

**Section heading:**
> A mission, end to end.

**Body intro (use as-is):**
> A user submits a goal: *Identify possible new planet candidates in this Milky Way observational dataset.* Here is what happens.

Render the steps as a numbered vertical list. Step numbers in electric cobalt mono. Each step has a short serif heading and a one-line body description.

1. **Intake.** The user's goal, dataset, and constraints enter the system.
2. **Decomposition.** An LLM breaks the goal into atomic tasks: validate dataset, identify anomalies, cluster patterns, compare to known planet indicators, test alternative explanations, score candidates, summarise leads.
3. **Admission.** Each task is checked for validity, capability availability, budget, and policy. Forbidden, redundant, or impossible tasks are rejected before they reach a drone.
4. **Routing.** Tasks are matched to drones by capability. Anomaly analysis goes to a data-analysis drone; literature comparison to a research drone; scoring to a reasoning-heavy drone.
5. **Execution.** Drones spawn, load the skills they need, do the work, emit findings, and terminate.
6. **Triage.** The Protector quarantines weak or unsupported findings. Strong findings pass through.
7. **Placement.** The Gatekeeper decides: raw anomaly note → Findings Graph; validated candidate score → Knowledge Graph; unsupported speculation → quarantine.
8. **Lifecycle.** The Sleeper prunes stale weak signals; high-value findings persist until the mission ends.
9. **Alignment.** The Aligner checks whether the actual output satisfies the original user goal. If not, new tasks are generated and the loop continues.
10. **Return.** When the goal is satisfied, results are returned. The drones are gone. The Knowledge Graph remains.

**Caption below (mono, small):**
> Sharp systems fail the same way humans do — by becoming bloated institutions. Drone Graph's discipline is to refuse that fate.

---

## 10. What it's for / what it isn't for

Two columns, side-by-side on desktop, stacked on mobile. Same visual weight on both — the honesty of the "isn't for" column is part of the brand.

**Left column — heading: "Use it for"**
- Multi-step, evidence-heavy research workflows
- Long-running discovery and analysis pipelines
- Large codebase analysis and repair
- Multi-source synthesis with verification
- Investigations where provenance matters
- Any work too complex for a single LLM call

**Right column — heading: "Don't use it for"**
- Basic chat
- One-shot Q&A
- Quick drafting or summarisation
- Small isolated tasks a single good model already handles

**Caption below both columns (mono, small, centred):**
> No point rolling out a battle tank to buy milk.

---

## 11. For founders / for researchers

Two columns, audience-specific benefits. Same visual treatment as the previous section.

**Left column — "If you're shipping an AI product"**
> Stop fighting your framework. Drone Graph handles the infrastructure no one else has built right — capability-aware routing, deterministic coordination, structured memory, provenance on every output — so your team can ship features instead of debugging hallucinated completion. Drones are disposable, so context never rots. Protocols are deterministic, so coordination is debuggable. Every output carries metadata, so you can prove what happened. The end of pretend AI employees.

**Right column — "If you're researching agent architecture"**
> Drone Graph is built around principled separations: coordination vs reasoning, execution vs identity, temporary vs durable memory, capability vs confidence. The three-graph memory model makes failure modes legible — you can point at which graph an error came from. Provenance is first-class. Termination is first-class. The architecture is opinionated, documented, and open. The collective intelligence layer for AI.

---

## 12. Open source call-out

Full-bleed band, electric cobalt background, cream type. Centred.

**Heading (cream serif, large):**
> Open by default. Built in the open. Read every line.

**Body (use as-is):**
> Drone Graph is open source. The architecture document, the protocol specifications, and the reference implementation are all public. We're building the operating system for autonomous swarms the way good infrastructure has always been built: in the open, with people who care about getting it right.

**Primary CTA button (cream fill, cobalt type):** `Fire your fake AI CEO` → href="#" placeholder.
**Secondary CTA (text link, cream, smaller):** `View on GitHub` → href="#"

---

## 13. Footer

Minimal. Mono type, ink at 60% opacity, centred.

- Wordmark: `Drone Graph` in serif
- Three links: `Architecture` · `GitHub` · `Contact`
- Tagline line in mono, very small: `The protocol layer for machine civilizations.`
- Copyright: `© 2026`

No newsletter signup. No social icons. No cookie modal unless legally required.

---

## 14. Technical requirements

- Single static page. No framework required; vanilla HTML/CSS is fine. If a framework is used, prefer Astro or plain Next.js static export.
- All copy in semantic HTML. Clean h1/h2/h3 hierarchy.
- Accessible: WCAG AA contrast minimum, keyboard navigable, alt text on every image (use the description text from each placeholder).
- Responsive: mobile-first. Single-column collapse for all multi-column sections. Hero illustration moves below the type on screens narrower than 900px.
- Performance: page weight under 200KB excluding the hero illustration. No tracking scripts. Self-host fonts.
- Dark mode: not required for v1. The inverse band in section 6 handles the contrast moment.
- Placeholders should be visually obvious — render them with a 1px dashed cobalt border and the placeholder text inside in monospace.

---

## 15. Out of scope

- Pricing page
- Documentation site
- Login / signup
- Demo modal or video
- Customer testimonials
- Live chat widget

These can come later. The landing page exists to land the thesis and send people to GitHub.
