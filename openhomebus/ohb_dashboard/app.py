"""OpenHomeBus Dashboard — aiohttp web application.

Provides:
  - REST API for device CRUD (backed by SQLite)
  - HA auto-discovery publish / remove per device
  - Diagnostics snapshot & history endpoints
  - WebSocket at ``/ws/bus`` for live state forwarding
  - Static file serving for the dashboard SPA
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

from . import __version__
from .const import DATA_DIR, DEVICE_TYPE_CONTROLLER, NODE_STATE_ONLINE
from .device_registry import DeviceRegistry
from .diagnostics import DiagnosticsManager
from .ha_discovery import (
    publish_discovery,
    remove_discovery,
    remove_controller_discovery,
)
from .mqtt_client import MQTTClient
from .settings import SettingsManager

_LOGGER = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent.parent / "dashboard"


# ── Application factory ───────────────────────────────────────────────────


def _discover_ha_mqtt() -> dict[str, Any]:
    """Try to get MQTT credentials from HA Supervisor service discovery."""
    import os

    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        return {}
    try:
        import urllib.request

        req = urllib.request.Request(
            "http://supervisor/services/mqtt",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            svc = data.get("data", {})
            result = {}
            if svc.get("host"):
                result["mqtt_broker"] = svc["host"]
            if svc.get("port"):
                result["mqtt_port"] = svc["port"]
            if svc.get("username"):
                result["mqtt_username"] = svc["username"]
            if svc.get("password"):
                result["mqtt_password"] = svc["password"]
            if result:
                _LOGGER.info(
                    "Auto-discovered MQTT from HA Supervisor: %s:%s",
                    result.get("mqtt_broker"),
                    result.get("mqtt_port"),
                )
            return result
    except Exception:
        _LOGGER.debug("HA Supervisor MQTT discovery not available")
        return {}


def create_app(ha_options: dict[str, Any] | None = None) -> web.Application:
    """Create and configure the aiohttp web application."""
    app = web.Application()
    opts = ha_options or {}

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── Shared components (stored in app dict) ─────────────────────────
    app["registry"] = DeviceRegistry()
    app["settings"] = SettingsManager()

    # Try HA Supervisor MQTT service discovery first
    ha_mqtt = _discover_ha_mqtt()

    # Effective MQTT config: ha_options → ha_mqtt discovery → settings → defaults
    mqtt_broker = (
        opts.get("mqtt_broker", "")
        or ha_mqtt.get("mqtt_broker", "")
        or app["settings"].get("mqtt_broker", "localhost")
    )
    mqtt_port = (
        opts.get("mqtt_port", 0)
        or ha_mqtt.get("mqtt_port", 0)
        or app["settings"].get("mqtt_port", 1883)
    )
    mqtt_username = (
        opts.get("mqtt_username", "")
        or ha_mqtt.get("mqtt_username", "")
        or app["settings"].get("mqtt_username", "")
    )
    mqtt_password = (
        opts.get("mqtt_password", "")
        or ha_mqtt.get("mqtt_password", "")
        or app["settings"].get("mqtt_password", "")
    )

    # Sync effective MQTT settings into SettingsManager so provisioning
    # and the settings API always have the current values.
    settings_to_sync = {}
    if mqtt_broker and not app["settings"].get("mqtt_broker"):
        settings_to_sync["mqtt_broker"] = mqtt_broker
    if mqtt_port and not app["settings"].get("mqtt_port"):
        settings_to_sync["mqtt_port"] = mqtt_port
    if mqtt_username and not app["settings"].get("mqtt_username"):
        settings_to_sync["mqtt_username"] = mqtt_username
    if mqtt_password and not app["settings"].get("mqtt_password"):
        settings_to_sync["mqtt_password"] = mqtt_password
    if settings_to_sync:
        app["settings"].update(settings_to_sync)
        _LOGGER.info(
            "Synced MQTT settings to persistent store: %s",
            list(settings_to_sync.keys()),
        )

    # Store resolved config in app dict for provisioning endpoint
    app["mqtt_config"] = {
        "mqtt_broker": mqtt_broker,
        "mqtt_port": mqtt_port,
        "mqtt_username": mqtt_username,
        "mqtt_password": mqtt_password,
    }

    app["mqtt"] = MQTTClient(
        broker=mqtt_broker,
        port=mqtt_port,
        username=mqtt_username,
        password=mqtt_password,
    )
    app["diagnostics"] = DiagnosticsManager()
    app["ws_clients"] = set()  # active WebSocket connections

    # ── Lifecycle hooks ────────────────────────────────────────────────
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)

    # ── Routes ─────────────────────────────────────────────────────────
    app.router.add_get("/", index_handler)
    app.router.add_get("/api/info", api_info)

    # Device CRUD
    app.router.add_get("/api/devices", api_devices_list)
    app.router.add_post("/api/devices", api_devices_add)
    app.router.add_get("/api/devices/{ctrl}/{addr}", api_device_get)
    app.router.add_put("/api/devices/{ctrl}/{addr}", api_device_update)
    app.router.add_delete("/api/devices/{ctrl}/{addr}", api_device_delete)

    # HA Discovery
    app.router.add_post("/api/devices/{ctrl}/{addr}/ha-discover", api_ha_discover)
    app.router.add_delete("/api/devices/{ctrl}/{addr}/ha-discover", api_ha_remove)

    # Diagnostics
    app.router.add_get("/api/diagnostics", api_diagnostics)
    app.router.add_get("/api/diagnostics/history", api_diagnostics_history)
    app.router.add_post("/api/diagnostics/reset", api_diagnostics_reset)
    app.router.add_get("/api/diagnostics/controllers", api_controllers_summary)
    app.router.add_get("/api/diagnostics/controllers/{ctrl}", api_controller_detail)
    app.router.add_get(
        "/api/diagnostics/controllers/{ctrl}/nodes", api_controller_nodes
    )
    app.router.add_get("/api/diagnostics/events", api_diagnostics_events)

    # Bus log
    app.router.add_get("/api/bus-log", api_bus_log)

    # MQTT status
    app.router.add_get("/api/mqtt/status", api_mqtt_status)

    # Settings
    app.router.add_get("/api/settings", api_settings_get)
    app.router.add_post("/api/settings", api_settings_save)

    # Controller discovery
    app.router.add_get("/api/controllers", api_controllers_list)
    app.router.add_get("/api/controllers/discover", api_controllers_discover)
    app.router.add_post("/api/controllers/provision", api_controllers_provision)
    app.router.add_delete("/api/controllers/{ctrl_id}", api_controller_delete)
    app.router.add_get("/api/controllers/{ip}/info", api_controller_info)

    # WebSocket for live state
    app.router.add_get("/ws/bus", ws_bus_handler)

    # Static dashboard files
    if STATIC_DIR.exists():
        app.router.add_static("/static", STATIC_DIR, name="static")

    return app


# ── Lifecycle ──────────────────────────────────────────────────────────────


async def _on_startup(app: web.Application) -> None:
    registry: DeviceRegistry = app["registry"]
    mqtt: MQTTClient = app["mqtt"]
    diag: DiagnosticsManager = app["diagnostics"]

    await registry.start()
    await mqtt.start()

    # Wire MQTT callbacks → diagnostics + registry + websocket
    mqtt.on_controller_status(diag.handle_controller_status)
    mqtt.on_diagnostics(diag.handle_diagnostics)
    mqtt.on_state_update(diag.handle_state)
    mqtt.on_state_update(_make_ws_forwarder(app))
    mqtt.on_node_event(_make_event_handler(app))
    mqtt.on_availability(_make_availability_handler(app))
    mqtt.on_controller_status(_make_controller_status_handler(app))

    # Background diagnostics checker
    app["_diag_task"] = asyncio.create_task(diag.start())
    _LOGGER.info("Dashboard started")


async def _on_cleanup(app: web.Application) -> None:
    diag: DiagnosticsManager = app["diagnostics"]
    diag.stop()
    task = app.get("_diag_task")
    if task:
        task.cancel()

    mqtt: MQTTClient = app["mqtt"]
    await mqtt.stop()

    registry: DeviceRegistry = app["registry"]
    await registry.stop()

    # Close WebSocket connections
    for ws in set(app["ws_clients"]):
        await ws.close()
    _LOGGER.info("Dashboard stopped")


# ── MQTT → app glue ───────────────────────────────────────────────────────


def _make_ws_forwarder(app: web.Application):
    """Return a callback that forwards state updates to all WebSocket clients."""

    def _forward(controller_id: str, addr: int, state: dict[str, Any]) -> None:
        msg = json.dumps(
            {
                "type": "state",
                "controller": controller_id,
                "address": addr,
                "state": state,
            }
        )
        for ws in set(app["ws_clients"]):
            asyncio.ensure_future(ws.send_str(msg))

    return _forward


def _make_event_handler(app: web.Application):
    """Return a callback that handles node join/leave events."""

    def _handle(controller_id: str, event: dict[str, Any]) -> None:
        registry: DeviceRegistry = app["registry"]
        event_type = event.get("event")
        addr = event.get("address", 0)
        if event_type == "join":
            asyncio.ensure_future(
                registry.add_device(
                    controller_id=controller_id,
                    address=addr,
                    hw_id=event.get("hw_id", ""),
                    device_type=event.get("device_type", 0x10),
                    fw_version=event.get("fw_version", ""),
                )
            )
            asyncio.ensure_future(
                registry.log_event(controller_id, "join", addr, event)
            )
        elif event_type == "leave":
            asyncio.ensure_future(registry.mark_offline(controller_id, addr))
            asyncio.ensure_future(
                registry.log_event(controller_id, "leave", addr, event)
            )

        # Forward to WS clients
        msg = json.dumps(
            {
                "type": "event",
                "controller": controller_id,
                "event": event,
            }
        )
        for ws in set(app["ws_clients"]):
            asyncio.ensure_future(ws.send_str(msg))

    return _handle


def _make_controller_status_handler(app: web.Application):
    """Return a callback that auto-registers controllers as devices."""

    def _handle(controller_id: str, status: dict[str, Any]) -> None:
        if not status.get("online", False):
            return
        registry: DeviceRegistry = app["registry"]

        async def _register():
            existing = await registry.get_device(controller_id, 0)
            if existing:
                # Already registered — just update last_seen and state
                await registry.mark_online(controller_id, 0)
                return
            # Auto-register the controller as address 0x00
            fw = status.get("fw_version", "")
            await registry.add_device(
                controller_id=controller_id,
                address=0,
                hw_id=f"{controller_id}_ctrl",
                device_type=DEVICE_TYPE_CONTROLLER,
                name=f"Controller {controller_id}",
                fw_version=fw,
            )
            _LOGGER.info("Auto-registered controller %s as device", controller_id)

        asyncio.ensure_future(_register())

        # Forward to WS clients
        msg = json.dumps(
            {
                "type": "controller_status",
                "controller": controller_id,
                "status": status,
            }
        )
        for ws in set(app["ws_clients"]):
            asyncio.ensure_future(ws.send_str(msg))

    return _handle


def _make_availability_handler(app: web.Application):
    """Return a callback that updates device online/offline state."""

    def _handle(controller_id: str, addr: int, available: bool) -> None:
        registry: DeviceRegistry = app["registry"]
        if available:
            asyncio.ensure_future(registry.mark_online(controller_id, addr))
        else:
            asyncio.ensure_future(registry.mark_offline(controller_id, addr))

    return _handle


# ── Handlers ───────────────────────────────────────────────────────────────


async def index_handler(request: web.Request) -> web.Response:
    """Serve the dashboard SPA with HA ingress base href."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        return web.Response(text="Dashboard: static files not found", status=500)

    ingress_path = request.headers.get("X-Ingress-Path", "")
    base_href = ingress_path.rstrip("/") + "/" if ingress_path else "/"

    html = index_path.read_text(encoding="utf-8")
    html = html.replace("<head>", f'<head>\n  <base href="{base_href}" />', 1)
    return web.Response(text=html, content_type="text/html")


async def api_info(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "name": "OpenHomeBus Dashboard",
            "version": __version__,
            "mqtt_connected": request.app["mqtt"].is_connected,
        }
    )


# ── Device CRUD ────────────────────────────────────────────────────────────


async def api_devices_list(request: web.Request) -> web.Response:
    registry: DeviceRegistry = request.app["registry"]
    devices = await registry.list_devices()
    # Filter out controller-type devices — they are shown on the Controllers page
    non_ctrl = [d for d in devices if d.device_type != DEVICE_TYPE_CONTROLLER]
    return web.json_response({"devices": [d.to_dict() for d in non_ctrl]})


async def api_devices_add(request: web.Request) -> web.Response:
    registry: DeviceRegistry = request.app["registry"]
    body = await request.json()

    ctrl = body.get("controller_id", "")
    addr = body.get("address", 0)
    hw_id = body.get("hw_id", "")
    device_type = body.get("device_type", 0x10)
    name = body.get("name", "")

    if not ctrl or not hw_id:
        return web.json_response(
            {"error": "controller_id and hw_id are required"}, status=400
        )

    entry = await registry.add_device(
        controller_id=ctrl,
        address=addr,
        hw_id=hw_id,
        device_type=device_type,
        name=name,
        fw_version=body.get("fw_version", ""),
    )
    return web.json_response(entry.to_dict(), status=201)


async def api_device_get(request: web.Request) -> web.Response:
    registry: DeviceRegistry = request.app["registry"]
    ctrl = request.match_info["ctrl"]
    addr = int(request.match_info["addr"], 16)
    entry = await registry.get_device(ctrl, addr)
    if not entry:
        return web.json_response({"error": "Device not found"}, status=404)
    return web.json_response(entry.to_dict())


async def api_device_update(request: web.Request) -> web.Response:
    registry: DeviceRegistry = request.app["registry"]
    ctrl = request.match_info["ctrl"]
    addr = int(request.match_info["addr"], 16)
    body = await request.json()
    entry = await registry.update_device(ctrl, addr, body)
    if not entry:
        return web.json_response({"error": "Device not found"}, status=404)
    return web.json_response(entry.to_dict())


async def api_device_delete(request: web.Request) -> web.Response:
    registry: DeviceRegistry = request.app["registry"]
    ctrl = request.match_info["ctrl"]
    addr = int(request.match_info["addr"], 16)

    # Remove HA discovery first
    entry = await registry.get_device(ctrl, addr)
    if entry:
        mqtt: MQTTClient = request.app["mqtt"]
        remove_discovery(mqtt, entry)

    if await registry.delete_device(ctrl, addr):
        return web.json_response({"ok": True})
    return web.json_response({"error": "Device not found"}, status=404)


# ── HA Discovery ───────────────────────────────────────────────────────────


async def api_ha_discover(request: web.Request) -> web.Response:
    registry: DeviceRegistry = request.app["registry"]
    mqtt: MQTTClient = request.app["mqtt"]
    ctrl = request.match_info["ctrl"]
    addr = int(request.match_info["addr"], 16)

    entry = await registry.get_device(ctrl, addr)
    if not entry:
        return web.json_response({"error": "Device not found"}, status=404)
    if not mqtt.is_connected:
        return web.json_response({"error": "MQTT not connected"}, status=503)

    count = publish_discovery(mqtt, entry)
    return web.json_response({"ok": True, "entities_published": count})


async def api_ha_remove(request: web.Request) -> web.Response:
    registry: DeviceRegistry = request.app["registry"]
    mqtt: MQTTClient = request.app["mqtt"]
    ctrl = request.match_info["ctrl"]
    addr = int(request.match_info["addr"], 16)

    entry = await registry.get_device(ctrl, addr)
    if not entry:
        return web.json_response({"error": "Device not found"}, status=404)

    count = remove_discovery(mqtt, entry)
    return web.json_response({"ok": True, "entities_removed": count})


# ── Diagnostics ────────────────────────────────────────────────────────────


async def api_diagnostics(request: web.Request) -> web.Response:
    diag: DiagnosticsManager = request.app["diagnostics"]
    return web.json_response(diag.get_snapshot())


async def api_diagnostics_history(request: web.Request) -> web.Response:
    diag: DiagnosticsManager = request.app["diagnostics"]
    ctrl = request.query.get("controller_id", "")
    n = int(request.query.get("last", "60"))
    if ctrl:
        return web.json_response({"history": diag.get_history(ctrl, n)})
    # Return history for all controllers
    all_hist = {}
    for cid in diag._controllers:
        all_hist[cid] = diag.get_history(cid, n)
    return web.json_response({"history": all_hist})


async def api_diagnostics_reset(request: web.Request) -> web.Response:
    diag: DiagnosticsManager = request.app["diagnostics"]
    body = await request.json() if request.content_length else {}
    ctrl = body.get("controller_id")
    diag.reset_counters(ctrl)
    return web.json_response({"ok": True})


async def api_controllers_summary(request: web.Request) -> web.Response:
    """Return a summary of all controllers and aggregate bus stats.

    Merges diagnostics data with device-registry state so that
    controllers discovered via MQTT status messages always reflect
    their registry online/offline state (event-driven) instead of
    relying solely on the 60-s heartbeat timeout in the diagnostics
    manager.
    """
    diag: DiagnosticsManager = request.app["diagnostics"]
    registry: DeviceRegistry = request.app["registry"]

    # Diagnostics keyed by controller_id
    diag_ctrls = {c["controller_id"]: c for c in diag.get_controllers_summary()}

    # Controller-type devices from the registry
    all_devs = await registry.list_devices()
    reg_ctrls = {
        d.controller_id: d for d in all_devs if d.device_type == DEVICE_TYPE_CONTROLLER
    }

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    # 1. Start from diagnostics entries and enrich with registry state
    for ctrl_id, dc in diag_ctrls.items():
        seen.add(ctrl_id)
        dev = reg_ctrls.get(ctrl_id)
        # Prefer registry state when diagnostics timed out
        if dev and dev.state == NODE_STATE_ONLINE and not dc["online"]:
            dc["online"] = True
        merged.append(dc)

    # 2. Controllers only known in the registry (no diagnostics yet)
    for ctrl_id, dev in reg_ctrls.items():
        if ctrl_id in seen:
            continue
        merged.append(
            {
                "controller_id": ctrl_id,
                "online": dev.state == NODE_STATE_ONLINE,
                "last_seen": 0,
                "uptime_s": 0,
                "uptime_str": "",
                "fw_version": dev.fw_version,
                "wifi_rssi": 0,
                "wifi_quality": "",
                "link_type": "",
                "ip_address": "",
                "free_heap": 0,
                "total_heap": 0,
                "heap_usage_pct": 0,
                "cpu_temp_c": 0,
                "restart_count": 0,
                "active_nodes": 0,
                "total_frames": 0,
                "total_errors": 0,
                "error_rate": 0,
                "bus_voltage": 0,
                "bus_utilization": 0,
                "token_cycle_ms": 0,
                "rx_frames": 0,
                "tx_frames": 0,
                "crc_errors": 0,
                "framing_errors": 0,
                "token_timeouts": 0,
            }
        )

    return web.json_response(
        {
            "controllers": merged,
            "bus_stats": diag.get_bus_stats(),
        }
    )


async def api_controller_detail(request: web.Request) -> web.Response:
    """Return detailed status for a single controller.

    Merges diagnostics data with the device-registry entry so the
    detail view has consistent online/offline state and device info.
    """
    diag: DiagnosticsManager = request.app["diagnostics"]
    registry: DeviceRegistry = request.app["registry"]
    ctrl_id = request.match_info["ctrl"]

    ctrl = diag.get_controller(ctrl_id)
    dev = await registry.get_device(ctrl_id, 0)

    if not ctrl and not dev:
        return web.json_response({"error": "Controller not found"}, status=404)

    if ctrl is None:
        # Build a minimal diagnostics-shaped dict from the registry
        ctrl = {
            "controller_id": ctrl_id,
            "online": dev.state == NODE_STATE_ONLINE if dev else False,
            "last_seen": 0,
            "uptime_s": 0,
            "uptime_str": "",
            "fw_version": dev.fw_version if dev else "",
            "wifi_rssi": 0,
            "wifi_quality": "",
            "free_heap": 0,
            "total_heap": 0,
            "heap_usage_pct": 0,
            "cpu_temp_c": 0,
            "restart_count": 0,
            "active_nodes": 0,
            "total_frames": 0,
            "total_errors": 0,
            "error_rate": 0,
            "bus_voltage": 0,
            "bus_utilization": 0,
            "token_cycle_ms": 0,
            "rx_frames": 0,
            "tx_frames": 0,
            "crc_errors": 0,
            "framing_errors": 0,
            "token_timeouts": 0,
        }
    elif dev and dev.state == NODE_STATE_ONLINE and not ctrl.get("online"):
        ctrl["online"] = True

    # Include device-registry info for the controller
    device_info = dev.to_dict() if dev else None

    nodes = diag.get_nodes_for_controller(ctrl_id)
    return web.json_response(
        {
            "controller": ctrl,
            "nodes": nodes,
            "device": device_info,
        }
    )


async def api_controller_nodes(request: web.Request) -> web.Response:
    """Return nodes for a specific controller."""
    diag: DiagnosticsManager = request.app["diagnostics"]
    ctrl_id = request.match_info["ctrl"]
    nodes = diag.get_nodes_for_controller(ctrl_id)
    return web.json_response({"nodes": nodes})


async def api_diagnostics_events(request: web.Request) -> web.Response:
    """Return recent bus events."""
    diag: DiagnosticsManager = request.app["diagnostics"]
    limit = int(request.query.get("limit", "50"))
    return web.json_response({"events": diag.get_events(limit)})


# ── Bus log ────────────────────────────────────────────────────────────────


async def api_bus_log(request: web.Request) -> web.Response:
    registry: DeviceRegistry = request.app["registry"]
    limit = int(request.query.get("limit", "100"))
    ctrl = request.query.get("controller_id")
    entries = await registry.get_log(limit=limit, controller_id=ctrl)
    return web.json_response({"log": entries})


# ── MQTT status ────────────────────────────────────────────────────────────


async def api_mqtt_status(request: web.Request) -> web.Response:
    mqtt: MQTTClient = request.app["mqtt"]
    return web.json_response({"connected": mqtt.is_connected})


# ── Settings ───────────────────────────────────────────────────────────────


async def api_settings_get(request: web.Request) -> web.Response:
    settings: SettingsManager = request.app["settings"]
    return web.json_response(settings.get_all())


async def api_settings_save(request: web.Request) -> web.Response:
    settings: SettingsManager = request.app["settings"]
    body = await request.json()

    settings.update(body)

    # Reconnect MQTT if broker settings changed
    mqtt: MQTTClient = request.app["mqtt"]
    broker = settings.get("mqtt_broker", "localhost")
    port = settings.get("mqtt_port", 1883)
    username = settings.get("mqtt_username", "")
    password = settings.get("mqtt_password", "")

    await mqtt.stop()
    mqtt._broker = broker
    mqtt._port = port
    mqtt._username = username
    mqtt._password = password
    await mqtt.start()

    return web.json_response({"ok": True})


# ── Controller Discovery ──────────────────────────────────────────────────


async def api_controllers_list(request: web.Request) -> web.Response:
    """Return cached list of discovered controllers."""
    controllers = request.app.get("controllers", [])
    return web.json_response({"controllers": controllers})


async def api_controllers_discover(request: web.Request) -> web.StreamResponse:
    """Scan the network for OHB controllers via mDNS + subnet HTTP scan.

    Returns results as Server-Sent Events so the dashboard can show progress:
      event: controller   → a discovered controller JSON
      event: progress     → {stage, pct}
      event: done         → final summary
    """
    resp = web.StreamResponse()
    resp.content_type = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    await resp.prepare(request)

    controllers: list[dict] = []
    seen_ips: set[str] = set()

    async def _send(event: str, data: dict) -> None:
        try:
            await resp.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
        except Exception:
            pass

    async def _send_progress(stage: str, pct: int) -> None:
        await _send("progress", {"stage": stage, "pct": pct})

    def _mark_provisioned(ctrl: dict) -> dict:
        """Set already_provisioned flag on a controller dict."""
        cid = ctrl.get("controller_id", "")
        if cid in diag._blocked:
            ctrl["already_provisioned"] = False
        else:
            ctrl["already_provisioned"] = (
                cid in known_ctrl_ids
                or ctrl.get("mqtt_connected", False)
                or ctrl.get("configured", False)
            )
        return ctrl

    diag: DiagnosticsManager = request.app["diagnostics"]
    known_ctrl_ids = set(diag._controllers.keys())

    # ── 1. mDNS discovery (fast — typically < 2s) ───────────────────────
    await _send_progress("mDNS discovery", 0)
    try:
        from zeroconf import Zeroconf, ServiceBrowser

        zc = Zeroconf()
        found: list[dict] = []

        class Listener:
            def add_service(self, zc_inst, svc_type, name):
                info = zc_inst.get_service_info(svc_type, name)
                if info:
                    addresses = info.parsed_addresses()
                    ip = addresses[0] if addresses else None
                    props = (
                        {k.decode(): v.decode() for k, v in info.properties.items()}
                        if info.properties
                        else {}
                    )
                    if ip:
                        found.append(
                            {
                                "name": name,
                                "ip": ip,
                                "port": info.port,
                                "controller_id": props.get("id", ""),
                                "fw_version": props.get("fw", ""),
                            }
                        )

            def remove_service(self, *args):
                pass

            def update_service(self, *args):
                pass

        _browser = ServiceBrowser(zc, "_ohb._tcp.local.", Listener())
        await asyncio.sleep(2)  # 2s is plenty for LAN mDNS
        zc.close()

        # Enrich mDNS results with HTTP /api/info (parallel)
        async def _enrich(ctrl: dict) -> dict:
            ip = ctrl.get("ip", "")
            if not ip:
                return ctrl
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=2)
                ) as session:
                    async with session.get(f"http://{ip}/api/info") as r:
                        if r.status == 200:
                            data = await r.json()
                            ctrl["controller_id"] = data.get(
                                "controller_id", ctrl.get("controller_id", "")
                            )
                            ctrl["fw_version"] = data.get(
                                "fw_version", ctrl.get("fw_version", "")
                            )
                            ctrl["mqtt_connected"] = data.get("mqtt_connected", False)
                            ctrl["configured"] = data.get("configured", False)
                            ctrl["active_nodes"] = data.get("active_nodes", 0)
            except Exception:
                pass
            return ctrl

        if found:
            enriched = await asyncio.gather(*[_enrich(c) for c in found])
            for ctrl in enriched:
                _mark_provisioned(ctrl)
                controllers.append(ctrl)
                seen_ips.add(ctrl["ip"])
                await _send("controller", ctrl)

    except ImportError:
        _LOGGER.warning("zeroconf not installed — skipping mDNS discovery")
    except Exception:
        _LOGGER.exception("mDNS discovery failed")

    await _send_progress("mDNS complete", 15)

    # ── 2. Known controllers (fast — just a few IPs) ────────────────────
    settings: SettingsManager = request.app["settings"]
    known_ips = settings.get("known_controllers", [])
    for ip in known_ips:
        if ip in seen_ips:
            continue
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=2)
            ) as session:
                async with session.get(f"http://{ip}/api/info") as r:
                    if r.status == 200:
                        info = await r.json()
                        ctrl = {
                            "ip": ip,
                            "port": 80,
                            "controller_id": info.get("controller_id", ""),
                            "fw_version": info.get("fw_version", ""),
                            "mqtt_connected": info.get("mqtt_connected", False),
                            "configured": info.get("configured", False),
                            "active_nodes": info.get("active_nodes", 0),
                        }
                        _mark_provisioned(ctrl)
                        controllers.append(ctrl)
                        seen_ips.add(ip)
                        await _send("controller", ctrl)
        except Exception:
            pass

    await _send_progress("Known controllers checked", 25)

    # ── 3. Subnet HTTP scan ─────────────────────────────────────────────
    try:
        import socket

        local_ip = socket.gethostbyname(socket.gethostname())
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass

        subnet_prefix = ".".join(local_ip.split(".")[:3])
        _LOGGER.info("Scanning subnet %s.0/24 for OHB controllers...", subnet_prefix)

        async def _probe(ip: str) -> dict | None:
            if ip in seen_ips or ip == local_ip:
                return None
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=0.8)
                ) as session:
                    async with session.get(f"http://{ip}/api/info") as r:
                        if r.status == 200:
                            data = await r.json()
                            if "controller_id" in data:
                                return {
                                    "ip": ip,
                                    "port": 80,
                                    "controller_id": data.get("controller_id", ""),
                                    "fw_version": data.get("fw_version", ""),
                                    "mqtt_connected": data.get("mqtt_connected", False),
                                    "configured": data.get("configured", False),
                                    "active_nodes": data.get("active_nodes", 0),
                                }
            except Exception:
                pass
            return None

        # Scan in batches of 85 — completes in ~3 batches
        batch_size = 85
        all_ips = list(range(1, 255))
        total_batches = (len(all_ips) + batch_size - 1) // batch_size
        for batch_idx, batch_start in enumerate(range(0, len(all_ips), batch_size)):
            batch = all_ips[batch_start : batch_start + batch_size]
            pct = 25 + int((batch_idx + 1) / total_batches * 75)
            tasks = [_probe(f"{subnet_prefix}.{i}") for i in batch]
            results = await asyncio.gather(*tasks)
            for r in results:
                if r:
                    _mark_provisioned(r)
                    controllers.append(r)
                    seen_ips.add(r["ip"])
                    await _send("controller", r)
            await _send_progress(f"Subnet scan ({batch_start + len(batch)}/254)", pct)

    except Exception:
        _LOGGER.exception("Subnet scan failed")

    # Cache results
    request.app["controllers"] = controllers

    await _send("done", {"total": len(controllers)})
    await resp.write_eof()
    return resp


async def api_controllers_provision(request: web.Request) -> web.Response:
    """Push MQTT config to a controller via its HTTP API."""
    body = await request.json()
    ip = body.get("ip", "")
    if not ip:
        return web.json_response({"error": "ip is required"}, status=400)

    # Use the resolved MQTT config (ha_options → discovery → settings → defaults)
    resolved = request.app.get("mqtt_config", {})
    settings: SettingsManager = request.app["settings"]
    broker = resolved.get("mqtt_broker") or settings.get("mqtt_broker", "localhost")
    mqtt_config = {
        "mqtt_broker": broker,
        "mqtt_port": (resolved.get("mqtt_port") or settings.get("mqtt_port", 1883)),
        "mqtt_user": (
            resolved.get("mqtt_username") or settings.get("mqtt_username", "")
        ),
        "mqtt_pass": (
            resolved.get("mqtt_password") or settings.get("mqtt_password", "")
        ),
    }

    # Override with request body if provided
    for key in ("mqtt_broker", "mqtt_port", "mqtt_user", "mqtt_pass", "controller_id"):
        if key in body:
            mqtt_config[key] = body[key]

    # Resolve the broker hostname to a LAN-routable IP — the ESP32 can't
    # resolve Docker-internal names like "core-mosquitto" and can't reach
    # Docker bridge IPs like 172.30.x.x.
    import socket

    broker_host = mqtt_config["mqtt_broker"]

    def _get_host_lan_ip() -> str | None:
        """Get the host machine's LAN IP (reachable from the ESP32)."""
        # Method 1: HA Supervisor API
        try:
            import os
            import urllib.request
            import json as _json

            token = os.environ.get("SUPERVISOR_TOKEN", "")
            if token:
                req = urllib.request.Request(
                    "http://supervisor/network/info",
                    headers={"Authorization": f"Bearer {token}"},
                )
                with urllib.request.urlopen(req, timeout=3) as resp:
                    data = _json.loads(resp.read())
                    for iface in data.get("data", {}).get("interfaces", []):
                        for addr_info in iface.get("ipv4", {}).get("address", []):
                            addr = addr_info.split("/")[0]
                            if not addr.startswith(("172.", "127.")):
                                return addr
        except Exception:
            pass
        # Method 2: UDP connect trick to find LAN-facing IP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip_addr = s.getsockname()[0]
            s.close()
            # Only use if it's not a Docker-internal address
            if not ip_addr.startswith(("172.", "127.")):
                return ip_addr
        except Exception:
            pass
        return None

    def _is_docker_internal(addr: str) -> bool:
        """Check if an IP is a Docker-internal address (unreachable from LAN)."""
        return addr.startswith(
            (
                "172.16.",
                "172.17.",
                "172.18.",
                "172.19.",
                "172.20.",
                "172.21.",
                "172.22.",
                "172.23.",
                "172.24.",
                "172.25.",
                "172.26.",
                "172.27.",
                "172.28.",
                "172.29.",
                "172.30.",
                "172.31.",
                "127.",
            )
        )

    resolved_ip = None
    try:
        resolved_ip = socket.gethostbyname(broker_host)
    except socket.gaierror:
        pass

    if resolved_ip and not _is_docker_internal(resolved_ip):
        # Resolved to a real LAN IP — use it directly
        mqtt_config["mqtt_broker"] = resolved_ip
        _LOGGER.info("Resolved MQTT broker '%s' → %s", broker_host, resolved_ip)
    else:
        # Resolved to a Docker-internal IP or failed — use the host's LAN IP
        # because MQTT addons expose their port on the host network
        lan_ip = _get_host_lan_ip()
        if lan_ip:
            mqtt_config["mqtt_broker"] = lan_ip
            _LOGGER.info(
                "MQTT broker '%s' resolved to Docker-internal %s — "
                "using host LAN IP %s instead",
                broker_host,
                resolved_ip or "(unresolved)",
                lan_ip,
            )
        elif resolved_ip:
            # Fallback: use the Docker IP (unlikely to work, but better than nothing)
            mqtt_config["mqtt_broker"] = resolved_ip
            _LOGGER.warning(
                "MQTT broker '%s' → %s (Docker-internal, may not work)",
                broker_host,
                resolved_ip,
            )
        else:
            _LOGGER.warning("Could not resolve MQTT broker '%s'", broker_host)

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as session:
            async with session.post(
                f"http://{ip}/api/config", json=mqtt_config
            ) as resp:
                result = await resp.json()

                # Remember this controller IP
                known = settings.get("known_controllers", [])
                if ip not in known:
                    known.append(ip)
                    settings.update({"known_controllers": known})

                # Unblock controller if it was previously removed
                # (so diagnostics will track it again once MQTT messages arrive)
                try:
                    async with session.get(f"http://{ip}/api/info") as info_resp:
                        if info_resp.status == 200:
                            info = await info_resp.json()
                            cid = info.get("controller_id", "")
                            if cid:
                                diag: DiagnosticsManager = request.app["diagnostics"]
                                diag.unblock_controller(cid)
                except Exception:
                    pass

                return web.json_response(result)
    except Exception as e:
        _LOGGER.exception("Failed to provision controller at %s", ip)
        return web.json_response(
            {"error": f"Failed to reach controller: {e}"}, status=502
        )


async def api_controller_info(request: web.Request) -> web.Response:
    """Proxy GET to a controller's /api/info."""
    ip = request.match_info["ip"]
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5)
        ) as session:
            async with session.get(f"http://{ip}/api/info") as resp:
                data = await resp.json()
                return web.json_response(data)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502)


async def api_controller_delete(request: web.Request) -> web.Response:
    """Remove a controller: clear HA discovery, tell ESP32 to disconnect, remove from diagnostics."""
    ctrl_id = request.match_info["ctrl_id"]
    mqtt: MQTTClient = request.app["mqtt"]
    diag: DiagnosticsManager = request.app["diagnostics"]
    registry: DeviceRegistry = request.app["registry"]
    settings: SettingsManager = request.app["settings"]

    removed_entities = 0

    # 1. Remove HA discovery for the controller's own sensors (firmware-published)
    if mqtt.is_connected:
        removed_entities += remove_controller_discovery(mqtt, ctrl_id)

    # 2. Remove HA discovery for any devices on this controller
    devices = await registry.list_devices()
    for dev in devices:
        if dev.controller_id != ctrl_id:
            continue
        if mqtt.is_connected:
            removed_entities += remove_discovery(mqtt, dev)
        await registry.delete_device(dev.controller_id, dev.address)

    # 3. Remove from diagnostics tracking (also blocks future MQTT messages)
    diag.remove_controller(ctrl_id)

    # 4. Find the controller's IP and tell it to clear MQTT config
    ctrl_ip = None
    cached = request.app.get("controllers", [])
    for c in cached:
        if c.get("controller_id") == ctrl_id:
            ctrl_ip = c.get("ip")
            break

    # Also try known_controllers list
    if not ctrl_ip:
        known = settings.get("known_controllers", [])
        for ip in known:
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=3)
                ) as session:
                    async with session.get(f"http://{ip}/api/info") as resp:
                        if resp.status == 200:
                            info = await resp.json()
                            if info.get("controller_id") == ctrl_id:
                                ctrl_ip = ip
                                break
            except Exception:
                pass

    mqtt_cleared = False
    if ctrl_ip:
        try:
            # Send empty broker to make the firmware call mqtt_bridge_stop()
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            ) as session:
                async with session.post(
                    f"http://{ctrl_ip}/api/config",
                    json={"mqtt_broker": "", "mqtt_port": 0},
                ) as resp:
                    if resp.status == 200:
                        mqtt_cleared = True
                        _LOGGER.info(
                            "Cleared MQTT config on controller %s (%s)",
                            ctrl_id,
                            ctrl_ip,
                        )
        except Exception:
            _LOGGER.warning(
                "Could not reach controller %s at %s to clear MQTT", ctrl_id, ctrl_ip
            )

    # 5. Remove from known controllers list
    known = settings.get("known_controllers", [])
    if ctrl_ip and ctrl_ip in known:
        known.remove(ctrl_ip)
        settings.update({"known_controllers": known})

    _LOGGER.info(
        "Deleted controller %s (%d HA entities removed, mqtt_cleared=%s)",
        ctrl_id,
        removed_entities,
        mqtt_cleared,
    )
    return web.json_response(
        {
            "ok": True,
            "entities_removed": removed_entities,
            "mqtt_cleared": mqtt_cleared,
        }
    )


# ── WebSocket ──────────────────────────────────────────────────────────────


async def ws_bus_handler(request: web.Request) -> web.WebSocketResponse:
    """WebSocket endpoint for live bus state updates.

    Clients receive JSON messages with types:
      ``state``  — node I/O state change
      ``event``  — node join / leave
      ``diag``   — diagnostics snapshot
    """
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    request.app["ws_clients"].add(ws)
    _LOGGER.debug("WebSocket client connected")

    # Also forward diagnostics to this client
    diag: DiagnosticsManager = request.app["diagnostics"]

    def _on_diag(snapshot: dict[str, Any]) -> None:
        if not ws.closed:
            asyncio.ensure_future(ws.send_str(json.dumps({"type": "diag", **snapshot})))

    diag.add_listener(_on_diag)

    try:
        async for msg in ws:
            # Clients may send commands in the future; ignore for now
            pass
    finally:
        diag.remove_listener(_on_diag)
        request.app["ws_clients"].discard(ws)
        _LOGGER.debug("WebSocket client disconnected")

    return ws
