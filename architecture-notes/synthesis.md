# Drone Graph — Architecture Overview

*Mission control for the hivemind.*

---

## Core Loop

A drone is awakened when a gap exists. It pulls what it needs from the collective mind, closes the gap, deposits findings, and dissolves. The process is infinite: gap finding always produces more gaps. After a gap tree reaches ~5 layers of depth, leaf gaps are exposed to drones for execution.

## Gaps

Gaps are the atomic unit of work — defined by absence, not assignment. Each gap carries:

- **Dependencies** — metadata and artifacts from tree decomposition; determines ordering
- **Required capabilities** — tool use, context window size, cost ceiling, latency tolerance, reasoning depth, model tier
- **Acceptance criteria** — how the hivemind knows the gap is closed

There are two kinds of gaps. **Preset gaps** are persistent and always present — they are never filled, only continually acted on. These include gap finding, memory management, research, and testing. All other gaps emerge from the gap finding process and are filled by drones as normal.

## Gap Finding

Gap finding is itself a preset gap. The drone working it does three things: decomposes levels of the infinite tree of gaps, identifies new gaps outside the tree that findings in the graph imply, and engages as needed with the user to surface new gaps. This replaces any notion of a centralized planner or decomposer — it's just another gap that drones work.

## Drones

Drones are fully ephemeral. They share one hivemind system prompt; everything else is injected via the user prompt per-task. A drone can set its own parameters (reasoning mode, token limit, temperature) — this is itself a tool/skill, not a privilege. Preset gaps require the highest-capability drones.

Drones can download and install new skills from the internet when needed, and can author entirely new skills themselves. Skills created by one drone are available to all future drones through the collective mind.

## Signal Protocol

To avoid conflicting work, drones follow a signal protocol: check whether a package is already installed before installing; don't open a file another drone has open. Coordination is mechanical, not managerial.

## The Terminal

The only real tool is the terminal. All drones connect to it and execute through it. When a drone installs a package (e.g. Playwright), it goes into the tool registry. Tools that haven't been used within a set time are uninstalled to save disk space.

## Collective Mind

The shared substrate. Stored locally. Only what's actively needed is loaded into memory (to save RAM); unused content is summarized and eventually pruned (to save disk). Contains:

- **Skills** — runnable procedures any drone can load, download, or create
- **Tool registry** — starts empty, populated as drones install packages via the terminal; stale tools are pruned
- **Findings** — venv state, research, business progress, execution history
- **User uploads** — files and context provided by the human
- **Gaps** — the full surface of open work (preset and emergent)

Everything in the collective mind is persisted, except what is explicitly pruned or summarized/compacted.

## Human Role

Humans provide direction (initial goal, answering questions, uploading files) and handle a limited set of things the hivemind cannot do on its own — adding funds, performing authenticated actions (login, passwords), and similar. The list is small by design; everything else is the hivemind's problem.
