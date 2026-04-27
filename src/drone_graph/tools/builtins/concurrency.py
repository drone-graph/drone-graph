"""Phase 3 coordination tools.

  * ``cm_acquire_file`` / ``cm_release_file`` — exclusive holds on a file path
    so two concurrent worker drones don't write to the same file at once.
  * ``cm_install_package`` — atomic check-and-install. If the package is
    already registered, the drone skips installing and gets the recorded
    usage example. Otherwise this drone runs the install commands and
    registers the result so future drones can find it.

All three are no-ops when the drone runs without a ``SignalStore``
(``ctx.signals is None``) — which is the default for the legacy
single-threaded ``run_combined_loop`` path. Under those conditions, the
tools return a clear "not available" message rather than silently passing.
"""

from __future__ import annotations

import json
from typing import Any

from drone_graph.terminal import TerminalDead, TerminalTimeout
from drone_graph.tools.registry import (
    DroneContext,
    ToolResult,
    register_tool,
)

DEFAULT_FILE_TTL_S = 300.0
DEFAULT_FILE_ACQUIRE_TIMEOUT_S = 30.0
DEFAULT_INSTALL_LOCK_TTL_S = 600.0
DEFAULT_INSTALL_COMMAND_TIMEOUT_S = 300.0


def _no_signals() -> ToolResult:
    return ToolResult(
        content=(
            "ERROR: this drone has no SignalStore attached — concurrency "
            "tools are unavailable. (Single-drone runs do not need them.)"
        )
    )


# ---- File claims ----------------------------------------------------------


@register_tool(
    "cm_acquire_file",
    (
        "Acquire an exclusive write claim on a file path so no concurrent "
        "drone overwrites your work. Returns success when the claim is "
        "yours. The claim auto-releases when this drone exits; call "
        "cm_release_file to release earlier. Mode 'read' is informational "
        "only and never blocks."
    ),
    {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute filesystem path.",
            },
            "mode": {
                "type": "string",
                "enum": ["write", "read"],
                "default": "write",
                "description": (
                    "'write' is exclusive (blocks other writers). 'read' is "
                    "informational and never blocks."
                ),
            },
            "timeout_s": {
                "type": "number",
                "default": DEFAULT_FILE_ACQUIRE_TIMEOUT_S,
                "description": "Block up to this many seconds for the claim.",
            },
        },
        "required": ["path"],
    },
)
def cm_acquire_file(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    if ctx.signals is None:
        return _no_signals()
    path = str(args.get("path", "")).strip()
    if not path:
        return ToolResult(content="ERROR: path is required")
    mode = str(args.get("mode", "write"))
    timeout_s = float(args.get("timeout_s", DEFAULT_FILE_ACQUIRE_TIMEOUT_S))
    if mode == "read":
        held = ctx.signals.get_claim("file", path)
        holder = held.drone_id if held else None
        return ToolResult(
            content=json.dumps(
                {
                    "path": path,
                    "mode": "read",
                    "current_writer": holder,
                }
            )
        )
    deadline = _monotonic() + max(0.0, timeout_s)
    while True:
        ok = ctx.signals.try_acquire(
            "file",
            path,
            ctx.drone_id,
            ttl_s=DEFAULT_FILE_TTL_S,
            metadata={"mode": "write"},
        )
        if ok:
            if ctx.tape is not None:
                ctx.tape.emit(
                    "tool.acquire_file",
                    drone_id=ctx.drone_id,
                    path=path,
                    mode="write",
                )
            return ToolResult(
                content=json.dumps(
                    {"path": path, "mode": "write", "acquired": True}
                )
            )
        if _monotonic() >= deadline:
            held = ctx.signals.get_claim("file", path)
            return ToolResult(
                content=json.dumps(
                    {
                        "path": path,
                        "acquired": False,
                        "reason": "timeout",
                        "current_writer": held.drone_id if held else None,
                    }
                )
            )
        _sleep(0.25)


@register_tool(
    "cm_release_file",
    (
        "Release a file claim this drone holds. No-op if not held. Always "
        "release as soon as the file write is complete so other drones "
        "aren't blocked."
    ),
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute filesystem path."},
        },
        "required": ["path"],
    },
)
def cm_release_file(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    if ctx.signals is None:
        return _no_signals()
    path = str(args.get("path", "")).strip()
    if not path:
        return ToolResult(content="ERROR: path is required")
    ctx.signals.release("file", path, ctx.drone_id)
    if ctx.tape is not None:
        ctx.tape.emit("tool.release_file", drone_id=ctx.drone_id, path=path)
    return ToolResult(
        content=json.dumps({"path": path, "released": True})
    )


# ---- Package install ------------------------------------------------------


@register_tool(
    "cm_install_package",
    (
        "Atomically install a package or system dependency. If another drone "
        "has already installed it (recorded in the install registry), this "
        "tool returns the recorded usage example and skips the install. "
        "Otherwise it acquires an install lock, runs the commands in this "
        "drone's terminal, and registers the result so future drones find "
        "it via cm_list_tools. Use a stable install_key like 'playwright' "
        "or 'pandas>=2.0'."
    ),
    {
        "type": "object",
        "properties": {
            "install_key": {
                "type": "string",
                "description": (
                    "Stable identifier for what's being installed. Used "
                    "across drones to dedupe."
                ),
            },
            "install_commands": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Shell commands to run, in order. Each must succeed "
                    "(exit code 0) for the install to count."
                ),
            },
            "usage": {
                "type": "string",
                "description": (
                    "Short usage example future drones will read from the "
                    "registry. E.g. an import line and a one-line call."
                ),
            },
        },
        "required": ["install_key", "install_commands"],
    },
)
def cm_install_package(args: dict[str, Any], ctx: DroneContext) -> ToolResult:
    if ctx.signals is None:
        return _no_signals()
    if ctx.terminal_box is None:
        return ToolResult(
            content="ERROR: cm_install_package requires a terminal-enabled drone"
        )
    install_key = str(args.get("install_key", "")).strip()
    cmds = [str(c) for c in args.get("install_commands", [])]
    usage = args.get("usage")
    usage_str = str(usage) if usage is not None else None
    if not install_key:
        return ToolResult(content="ERROR: install_key is required")
    if not cmds:
        return ToolResult(content="ERROR: at least one install_command is required")

    existing = ctx.signals.install_lookup(install_key)
    if existing is not None:
        return ToolResult(
            content=json.dumps(
                {
                    "install_key": install_key,
                    "already_installed": True,
                    "installed_by": existing.installed_by,
                    "install_commands": existing.install_commands,
                    "usage": existing.usage,
                }
            )
        )

    acquired = ctx.signals.try_acquire(
        "install",
        install_key,
        ctx.drone_id,
        ttl_s=DEFAULT_INSTALL_LOCK_TTL_S,
        metadata={"commands": cmds},
    )
    if not acquired:
        # Re-check the registry — another drone may have just registered.
        existing = ctx.signals.install_lookup(install_key)
        if existing is not None:
            return ToolResult(
                content=json.dumps(
                    {
                        "install_key": install_key,
                        "already_installed": True,
                        "installed_by": existing.installed_by,
                        "install_commands": existing.install_commands,
                        "usage": existing.usage,
                    }
                )
            )
        held = ctx.signals.get_claim("install", install_key)
        return ToolResult(
            content=json.dumps(
                {
                    "install_key": install_key,
                    "acquired": False,
                    "reason": "another drone is installing — wait and retry",
                    "current_installer": held.drone_id if held else None,
                }
            )
        )

    if ctx.tape is not None:
        ctx.tape.emit(
            "tool.install.start",
            drone_id=ctx.drone_id,
            install_key=install_key,
            n_commands=len(cmds),
        )

    terminal = ctx.terminal_box.get()
    for idx, cmd in enumerate(cmds, start=1):
        try:
            r = terminal.run(cmd, timeout=DEFAULT_INSTALL_COMMAND_TIMEOUT_S)
        except TerminalTimeout as e:
            ctx.signals.release("install", install_key, ctx.drone_id)
            return ToolResult(
                content=json.dumps(
                    {
                        "install_key": install_key,
                        "ok": False,
                        "failed_step": idx,
                        "cmd": cmd,
                        "error": f"timeout: {e}",
                    }
                )
            )
        except TerminalDead as e:
            ctx.terminal_box.respawn()
            ctx.signals.release("install", install_key, ctx.drone_id)
            return ToolResult(
                content=json.dumps(
                    {
                        "install_key": install_key,
                        "ok": False,
                        "failed_step": idx,
                        "cmd": cmd,
                        "error": f"terminal died: {e}",
                    }
                )
            )
        if r.exit_code != 0:
            ctx.signals.release("install", install_key, ctx.drone_id)
            if ctx.tape is not None:
                ctx.tape.emit(
                    "tool.install.failed",
                    drone_id=ctx.drone_id,
                    install_key=install_key,
                    failed_step=idx,
                    exit_code=r.exit_code,
                )
            return ToolResult(
                content=json.dumps(
                    {
                        "install_key": install_key,
                        "ok": False,
                        "failed_step": idx,
                        "cmd": cmd,
                        "exit_code": r.exit_code,
                        "stdout": r.stdout[-2000:],
                        "stderr": r.stderr[-2000:],
                    }
                )
            )

    registered = ctx.signals.install_register(
        install_key,
        ctx.drone_id,
        install_commands=cmds,
        usage=usage_str,
    )
    if not registered:
        # Another drone won the registration race after we ran our commands.
        existing = ctx.signals.install_lookup(install_key)
        if ctx.tape is not None:
            ctx.tape.emit(
                "tool.install.race_lost",
                drone_id=ctx.drone_id,
                install_key=install_key,
            )
        return ToolResult(
            content=json.dumps(
                {
                    "install_key": install_key,
                    "ok": True,
                    "duplicate": True,
                    "winner": existing.installed_by if existing else None,
                }
            )
        )
    if ctx.tape is not None:
        ctx.tape.emit(
            "tool.install.registered",
            drone_id=ctx.drone_id,
            install_key=install_key,
        )
    return ToolResult(
        content=json.dumps(
            {
                "install_key": install_key,
                "ok": True,
                "installed_by": ctx.drone_id,
                "usage": usage_str,
            }
        )
    )


# Indirected to make monkeypatching trivial in tests.
def _monotonic() -> float:
    import time as _time

    return _time.monotonic()


def _sleep(s: float) -> None:
    import time as _time

    _time.sleep(s)
