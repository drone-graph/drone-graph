"""FastAPI app factory + ``uvicorn`` entry point.

Loaded by ``drone-graph serve``. Owns the lifecycle of the substrate
connection, signal store, swarm controller, and SSE event bus.

The startup path is **two-phase**:

  1. **Always**: open the substrate, mirror builtins, wire the event bus, load
     operator settings, mount the API. The server boots even when no provider
     keys are present.
  2. **Conditional**: if Settings have keys (or env has them), construct the
     SwarmController and start its scheduler thread. If not, the API stays in
     a "needs setup" state until ``POST /api/settings`` lands a key.

This keeps ``drone-graph serve`` a single command that's safe to run from a
fresh checkout.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from drone_graph.api import settings as cfg
from drone_graph.api.control import SwarmController
from drone_graph.api.events import EventBus
from drone_graph.api.routers import chat as chat_router
from drone_graph.api.routers import control as control_router
from drone_graph.api.routers import edit as edit_router
from drone_graph.api.routers import permissions as permissions_router
from drone_graph.api.routers import profiles as profiles_router
from drone_graph.api.routers import settings as settings_router
from drone_graph.api.routers import stream as stream_router
from drone_graph.api.routers import substrate as substrate_router
from drone_graph.api.state import AppState, clear_state, get_state, set_state
from drone_graph.orchestrator import init_collective_mind
from drone_graph.signals import SQLiteSignalStore, default_db_path
from drone_graph.substrate import Substrate

# ---- Frontend serving ------------------------------------------------------

_PKG_ROOT = Path(__file__).resolve().parent.parent.parent.parent

_WEB_DIST_CANDIDATES = [
    _PKG_ROOT / "web" / "dist",
    Path("web") / "dist",
]


def _find_web_dist() -> Path | None:
    for p in _WEB_DIST_CANDIDATES:
        if (p / "index.html").exists():
            return p
    return None


# ---- Controller bootstrap --------------------------------------------------


def maybe_start_controller(settings: cfg.Settings | None = None) -> bool:
    """Try to construct the ``SwarmController`` from the current state +
    settings. Returns True if the controller is running (newly or already)."""
    state = get_state()
    if state.controller is not None:
        return True

    s = settings or cfg.load_settings()
    cfg.apply_to_env(s)
    if not cfg.has_any_key(s):
        return False

    # Lazy import — providers pulls in anthropic/openai SDKs.
    from drone_graph.drones import Provider, resolve_orchestrator_provider_model

    prov_enum = Provider(s.default_provider) if s.default_provider else None
    try:
        resolved_provider, resolved_model = resolve_orchestrator_provider_model(
            prov_enum, s.default_model
        )
    except ValueError:
        return False

    controller = SwarmController(
        substrate=state.substrate,
        store=state.store,
        tool_store=state.tool_store,
        signals=state.signals,
        provider=resolved_provider,
        model=resolved_model,
        event_bus=state.event_bus,
        tier_overrides=s.tier_overrides,
        workspace_dir=Path(cfg._effective_workspace_dir(s)),
    )
    if s.default_cost_ceiling_usd is not None:
        controller.set_cost_ceiling(s.default_cost_ceiling_usd)
    if s.default_paranoid_install:
        controller.set_paranoid_install(True)

    state.controller = controller
    state.provider_name = resolved_provider.value
    state.model = resolved_model
    state.event_bus.publish(
        "controller.ready",
        provider=resolved_provider.value,
        model=resolved_model,
    )
    return True


# ---- App factory -----------------------------------------------------------


def build_app(
    *,
    provider: str | None = None,
    model: str | None = None,
    cost_ceiling_usd: float | None = None,
    signal_db: Path | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        # Apply persisted settings before anything else looks at the env.
        settings = cfg.load_settings()
        cfg.apply_to_env(settings)
        # CLI flags override stored settings for this run only.
        if provider:
            settings.default_provider = provider
        if model:
            settings.default_model = model
        if cost_ceiling_usd is not None:
            settings.default_cost_ceiling_usd = cost_ceiling_usd

        substrate = _resolve_substrate()
        store, tool_store = init_collective_mind(substrate)
        signals = SQLiteSignalStore(signal_db or default_db_path())
        bus = EventBus()
        bus.attach_loop(asyncio.get_running_loop())

        set_state(
            AppState(
                substrate=substrate,
                store=store,
                tool_store=tool_store,
                signals=signals,
                event_bus=bus,
                controller=None,
                provider_name=None,
                model=None,
            )
        )

        # Try to start the swarm if keys are configured. If not, the server
        # serves Settings only until keys land.
        started = maybe_start_controller(settings)
        if not started:
            print(
                "[mission-control] no provider keys configured — open the UI "
                "to enter them in Settings.",
                file=sys.stderr,
            )
        try:
            yield
        finally:
            s = get_state()
            try:
                if s.controller is not None:
                    s.controller.shutdown()
            finally:
                try:
                    bus.stop()
                finally:
                    try:
                        signals.close()
                    finally:
                        substrate.close()
            clear_state()

    app = FastAPI(
        title="Drone Graph — Mission Control",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(substrate_router.router)
    app.include_router(edit_router.router)
    app.include_router(control_router.router)
    app.include_router(stream_router.router)
    app.include_router(settings_router.router)
    app.include_router(chat_router.router)
    app.include_router(permissions_router.router)
    app.include_router(profiles_router.router)

    dist = _find_web_dist()
    if dist is not None:
        app.mount(
            "/assets",
            StaticFiles(directory=dist / "assets"),
            name="assets",
        )

        @app.get("/")
        def _root() -> FileResponse:
            return FileResponse(dist / "index.html")

        @app.get("/{rest:path}")
        def _spa(rest: str) -> FileResponse:
            return FileResponse(dist / "index.html")

    else:
        @app.get("/")
        def _no_build() -> dict[str, str]:
            return {
                "message": (
                    "Mission control backend running. Frontend not built. Run "
                    "`drone-graph serve` (which auto-builds), or run `cd web && "
                    "npm install && npm run build` manually."
                ),
            }

    return app


def _resolve_substrate() -> Substrate:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "drone-graph-dev")
    return Substrate(uri, user, password)


# ---- Auto-bringup helpers (serve()) ----------------------------------------


def _ensure_frontend_built() -> Path | None:
    """If the frontend dist isn't there, try to build it. Idempotent."""
    dist = _find_web_dist()
    if dist is not None:
        return dist
    web_root = _PKG_ROOT / "web"
    if not (web_root / "package.json").exists():
        print(
            "[mission-control] web/package.json not found; can't auto-build. "
            "Skipping — the API will run without a UI.",
            file=sys.stderr,
        )
        return None
    npm = shutil.which("npm")
    if npm is None:
        print(
            "[mission-control] npm not on PATH — install Node.js or build the "
            "frontend yourself.",
            file=sys.stderr,
        )
        return None
    node_modules = web_root / "node_modules"
    if not node_modules.exists():
        print("[mission-control] installing frontend deps (one-time)…", file=sys.stderr)
        try:
            subprocess.run(
                [npm, "install", "--no-audit", "--no-fund", "--loglevel=error"],
                cwd=web_root,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"[mission-control] npm install failed: {e}", file=sys.stderr)
            return None
    print("[mission-control] building frontend…", file=sys.stderr)
    try:
        subprocess.run(
            [npm, "run", "build", "--silent"],
            cwd=web_root,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"[mission-control] frontend build failed: {e}", file=sys.stderr)
        return None
    return _find_web_dist()


def _neo4j_reachable(host: str, port: int, timeout_s: float = 1.0) -> bool:
    """TCP-only liveness check — fast, but doesn't prove Bolt is ready."""
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _neo4j_bolt_ready(uri: str, timeout_s: float = 2.0) -> bool:
    """Real Bolt handshake check. The TCP port opens before the Bolt
    protocol is initialised — startup races showed up here as
    "Connection to 127.0.0.1:7687 closed with incomplete handshake
    response". We probe by opening a driver, running a trivial query,
    and tearing down. Returns False on any failure."""
    try:
        from neo4j import GraphDatabase  # local import — heavy

        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "drone-graph-dev")
        driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            connection_acquisition_timeout=timeout_s,
            connection_timeout=timeout_s,
            max_connection_lifetime=5,
        )
        try:
            with driver.session() as session:
                list(session.run("RETURN 1 AS x"))
            return True
        finally:
            driver.close()
    except Exception:
        return False


def _ensure_neo4j() -> bool:
    """Best-effort: bring Neo4j up via docker compose if it isn't reachable.

    Liveness is checked at two levels: TCP for the cheap path (port
    open?) and a real Bolt handshake before returning success (so the
    caller doesn't race the substrate.init_schema query against a
    half-initialised database)."""
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    host, port = _parse_bolt(uri)
    if _neo4j_reachable(host, port) and _neo4j_bolt_ready(uri):
        return True
    compose_file = _PKG_ROOT / "docker-compose.yml"
    if not compose_file.exists():
        print(
            f"[mission-control] Neo4j not reachable at {host}:{port} and no "
            "docker-compose.yml found — start Neo4j manually.",
            file=sys.stderr,
        )
        return False
    docker = shutil.which("docker")
    if docker is None:
        print(
            f"[mission-control] Neo4j not reachable at {host}:{port} and "
            "docker isn't on PATH — start Colima or Docker Desktop and rerun.",
            file=sys.stderr,
        )
        return False
    print("[mission-control] bringing up Neo4j via docker compose…", file=sys.stderr)
    try:
        subprocess.run(
            [docker, "compose", "up", "-d", "neo4j"],
            cwd=compose_file.parent,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"[mission-control] docker compose up failed: {e}", file=sys.stderr)
        return False
    # Wait for Bolt — not just the TCP port. Neo4j listens on 7687 ~10
    # seconds before the Bolt protocol is actually ready; on a cold
    # container start the gap is closer to 20-30 seconds.
    deadline = time.time() + 90.0
    print("[mission-control] waiting for Neo4j Bolt to be ready…", file=sys.stderr)
    while time.time() < deadline:
        if _neo4j_reachable(host, port) and _neo4j_bolt_ready(uri):
            return True
        time.sleep(1.0)
    print(
        f"[mission-control] Neo4j Bolt didn't respond at {host}:{port} after 90s — "
        "check `docker compose logs neo4j`.",
        file=sys.stderr,
    )
    return False


def _parse_bolt(uri: str) -> tuple[str, int]:
    rest = uri.split("://", 1)[1] if "://" in uri else uri
    if ":" in rest:
        host, port = rest.split(":", 1)
        try:
            return host, int(port)
        except ValueError:
            return host, 7687
    return rest, 7687


# ---- Logging ---------------------------------------------------------------


def _configure_logging() -> None:
    """Set up a root StreamHandler so every module-level logger actually emits.

    Uvicorn ships its own dictConfig that replaces the root logger. We
    call this *before* uvicorn starts and pass ``log_config=None`` so our
    configuration survives and application logs (scheduler, drones, tools)
    appear alongside uvicorn's access log.
    """
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
            stream=sys.stderr,
        )
    # Silence the extremely noisy watchfiles re-scanner (one INFO line
    # per second in reload mode). Keep uvicorn.error for startup messages.
    logging.getLogger("watchfiles.main").setLevel(logging.WARNING)
    # Suppress chatty Neo4j "constraint already exists" INFO notifications.
    # The bootstrap runs CREATE CONSTRAINT ... IF NOT EXISTS which produces
    # harmless GqlStatusObject notices every startup.
    logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)


# ---- Entry point -----------------------------------------------------------


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    provider: str | None = None,
    model: str | None = None,
    cost_ceiling_usd: float | None = None,
    signal_db: Path | None = None,
    reload: bool = False,
    skip_bringup: bool = False,
) -> None:
    """Run uvicorn with our app. Called by the ``drone-graph serve`` CLI.

    Performs best-effort auto-bringup before listening:
      - Auto-build the frontend if ``web/dist`` is missing.
      - Bring up Neo4j via ``docker compose`` if the bolt port isn't open.
    Either can be skipped with ``--skip-bringup``.
    """
    import uvicorn

    if not skip_bringup:
        _ensure_frontend_built()
        _ensure_neo4j()

    _configure_logging()
    if reload:
        os.environ["DRONE_GRAPH_API_PROVIDER"] = provider or ""
        os.environ["DRONE_GRAPH_API_MODEL"] = model or ""
        if cost_ceiling_usd is not None:
            os.environ["DRONE_GRAPH_API_CEILING_USD"] = str(cost_ceiling_usd)
        if signal_db is not None:
            os.environ["DRONE_GRAPH_API_SIGNAL_DB"] = str(signal_db)
        uvicorn.run(
            "drone_graph.api.app:_reloadable_app",
            factory=True,
            host=host,
            port=port,
            reload=True,
            log_config=None,
        )
        return

    app = build_app(
        provider=provider,
        model=model,
        cost_ceiling_usd=cost_ceiling_usd,
        signal_db=signal_db,
    )
    print(f"[mission-control] http://{host}:{port}", file=sys.stderr)
    # Filter out the high-frequency polling endpoints from uvicorn's
    # access log. Otherwise the terminal drowns in identical
    # ``/api/drones/active`` and ``/api/snapshot`` 200s, masking real
    # signal (errors, drone events emitted by the scheduler tape).
    _install_uvicorn_access_filter()
    uvicorn.run(app, host=host, port=port, reload=False, log_config=None)


_POLL_ENDPOINTS_TO_HIDE = (
    "/api/drones/active",
    "/api/snapshot",
    "/api/status",
    "/api/inbox",
    "/api/settings",
)


def _install_uvicorn_access_filter() -> None:
    """Silence the uvicorn access log for endpoints the frontend polls on
    a sub-second cadence. Real errors (4xx/5xx) still log because the
    filter keys on the request line, and 500-class entries include a
    Traceback above the access line anyway."""
    import logging

    class _PollFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            try:
                msg = record.getMessage()
            except Exception:
                return True
            for ep in _POLL_ENDPOINTS_TO_HIDE:
                # Only suppress 2xx polling lines. A 500/404 on the same
                # URL still gets logged because the status code in the
                # access string would be different.
                if f'"GET {ep} HTTP/1.1" 200' in msg:
                    return False
                if f'"GET {ep} HTTP/1.1" 304' in msg:
                    return False
            return True

    logging.getLogger("uvicorn.access").addFilter(_PollFilter())


def _reloadable_app() -> FastAPI:
    """Builds an app using env vars (for uvicorn's reload mode)."""
    return build_app(
        provider=os.environ.get("DRONE_GRAPH_API_PROVIDER") or None,
        model=os.environ.get("DRONE_GRAPH_API_MODEL") or None,
        cost_ceiling_usd=(
            float(os.environ["DRONE_GRAPH_API_CEILING_USD"])
            if os.environ.get("DRONE_GRAPH_API_CEILING_USD")
            else None
        ),
        signal_db=(
            Path(os.environ["DRONE_GRAPH_API_SIGNAL_DB"])
            if os.environ.get("DRONE_GRAPH_API_SIGNAL_DB")
            else None
        ),
    )
