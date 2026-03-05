"""OpenHomeBus Dashboard -- Per-controller diagnostics module.

Tracks per-controller status (uptime, WiFi, heap, firmware),
per-node bus statistics, and rolling history for the dashboard.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

_LOGGER = logging.getLogger(__name__)

# How many history points to keep (5 min @ 1 report/s)
_MAX_HISTORY = 300
# Controller / node considered offline after this many seconds
_OFFLINE_THRESHOLD = 60.0
_NODE_OFFLINE_THRESHOLD = 30.0


# -- Helpers -----------------------------------------------------------------


def _format_uptime(seconds: int) -> str:
    """Human-readable uptime string."""
    if seconds <= 0:
        return "\u2014"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if days > 0:
        parts = [f"{days}d"]
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        return " ".join(parts)
    if hours > 0:
        return f"{hours}h"
    if minutes > 0:
        return f"{minutes}m"
    return f"{secs}s"


def _health_level(value: float, warn_threshold: float, crit_threshold: float) -> str:
    """Classify a metric value as good, warning, or critical."""
    if value >= crit_threshold:
        return "critical"
    if value >= warn_threshold:
        return "warning"
    return "good"


# -- Data classes ------------------------------------------------------------


@dataclass
class NodeDiagnostics:
    """Per-node diagnostics counters received from the controller."""

    address: int = 0
    hw_id: str = ""
    last_seen: float = 0.0
    rx_frames: int = 0
    tx_frames: int = 0
    crc_errors: int = 0
    timeouts: int = 0
    uptime_s: int = 0
    response_time_ms: float = 0.0

    @property
    def is_online(self) -> bool:
        return (time.time() - self.last_seen) < _NODE_OFFLINE_THRESHOLD

    @property
    def error_rate(self) -> float:
        total = self.rx_frames + self.tx_frames
        if total == 0:
            return 0.0
        return round((self.crc_errors / total) * 100, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "hw_id": self.hw_id,
            "online": self.is_online,
            "last_seen": self.last_seen,
            "rx_frames": self.rx_frames,
            "tx_frames": self.tx_frames,
            "crc_errors": self.crc_errors,
            "timeouts": self.timeouts,
            "uptime_s": self.uptime_s,
            "response_time_ms": self.response_time_ms,
            "error_rate": self.error_rate,
        }


@dataclass
class ControllerStatus:
    """Per-controller status and bus diagnostics."""

    controller_id: str = ""
    online: bool = False
    last_seen: float = 0.0
    uptime_s: int = 0
    fw_version: str = ""
    wifi_rssi: int = 0
    free_heap: int = 0
    total_heap: int = 0
    cpu_temp_c: float = 0.0
    restart_count: int = 0
    active_nodes: int = 0
    total_frames: int = 0
    total_errors: int = 0
    bus_voltage_v: float = 0.0
    bus_utilization_pct: float = 0.0
    token_cycle_ms: float = 0.0
    rx_frames: int = 0
    tx_frames: int = 0
    crc_errors: int = 0
    framing_errors: int = 0
    token_timeouts: int = 0

    @property
    def is_online(self) -> bool:
        return (time.time() - self.last_seen) < _OFFLINE_THRESHOLD

    @property
    def error_rate(self) -> float:
        if self.total_frames == 0:
            return 0.0
        return round((self.total_errors / self.total_frames) * 100, 2)

    @property
    def heap_usage_pct(self) -> float:
        if self.total_heap == 0:
            return 0.0
        return round(((self.total_heap - self.free_heap) / self.total_heap) * 100, 1)

    def _rssi_to_quality(self) -> str:
        if self.wifi_rssi >= -50:
            return "excellent"
        if self.wifi_rssi >= -60:
            return "good"
        if self.wifi_rssi >= -70:
            return "fair"
        return "poor"

    def to_dict(self) -> dict[str, Any]:
        return {
            "controller_id": self.controller_id,
            "online": self.is_online,
            "last_seen": self.last_seen,
            "uptime_s": self.uptime_s,
            "uptime_str": _format_uptime(self.uptime_s),
            "fw_version": self.fw_version,
            "wifi_rssi": self.wifi_rssi,
            "wifi_quality": self._rssi_to_quality(),
            "free_heap": self.free_heap,
            "total_heap": self.total_heap,
            "heap_usage_pct": self.heap_usage_pct,
            "cpu_temp_c": self.cpu_temp_c,
            "restart_count": self.restart_count,
            "active_nodes": self.active_nodes,
            "total_frames": self.total_frames,
            "total_errors": self.total_errors,
            "error_rate": self.error_rate,
            "bus_voltage": self.bus_voltage_v,
            "bus_utilization": self.bus_utilization_pct,
            "token_cycle_ms": self.token_cycle_ms,
            "rx_frames": self.rx_frames,
            "tx_frames": self.tx_frames,
            "crc_errors": self.crc_errors,
            "framing_errors": self.framing_errors,
            "token_timeouts": self.token_timeouts,
        }


@dataclass
class BusEvent:
    """A bus-level event (join, leave, error, etc.)."""

    timestamp: float = 0.0
    controller_id: str = ""
    event_type: str = ""
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        ago_s = time.time() - self.timestamp
        if ago_s < 60:
            ago = f"{int(ago_s)}s ago"
        elif ago_s < 3600:
            ago = f"{int(ago_s // 60)}m ago"
        else:
            ago = f"{int(ago_s // 3600)}h ago"

        return {
            "timestamp": self.timestamp,
            "controller_id": self.controller_id,
            "event_type": self.event_type,
            "message": self.message,
            "data": self.data,
            "ago": ago,
        }


# -- Main manager -----------------------------------------------------------


class DiagnosticsManager:
    """Per-controller diagnostics collector.

    Tracks controllers, their nodes, bus events, and rolling history.
    """

    def __init__(self) -> None:
        self._controllers: dict[str, ControllerStatus] = {}
        self._nodes: dict[str, dict[int, NodeDiagnostics]] = {}
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._events: list[BusEvent] = []
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._blocked: set[str] = set()  # controller IDs to ignore
        self._running = False

    # -- MQTT callbacks ------------------------------------------------------

    def handle_controller_status(
        self, controller_id: str, data: dict[str, Any]
    ) -> None:
        """Handle ohb/{ctrl}/status messages."""
        if controller_id in self._blocked:
            return
        ctrl = self._controllers.get(controller_id)
        if ctrl is None:
            ctrl = ControllerStatus(controller_id=controller_id)
            self._controllers[controller_id] = ctrl

        ctrl.online = data.get("online", ctrl.online)
        ctrl.last_seen = time.time()
        ctrl.uptime_s = data.get("uptime", ctrl.uptime_s)
        ctrl.fw_version = data.get("fw_version", ctrl.fw_version)
        ctrl.wifi_rssi = data.get("wifi_rssi", ctrl.wifi_rssi)
        ctrl.free_heap = data.get("free_heap", ctrl.free_heap)
        ctrl.total_heap = data.get("total_heap", ctrl.total_heap)
        ctrl.cpu_temp_c = data.get("cpu_temp", ctrl.cpu_temp_c)
        ctrl.restart_count = data.get("restart_count", ctrl.restart_count)
        ctrl.active_nodes = data.get("active_nodes", ctrl.active_nodes)

        self._notify_listeners()

    def handle_diagnostics(self, controller_id: str, data: dict[str, Any]) -> None:
        """Handle ohb/{ctrl}/diagnostics messages."""
        if controller_id in self._blocked:
            return
        ctrl = self._controllers.get(controller_id)
        if ctrl is None:
            ctrl = ControllerStatus(controller_id=controller_id)
            self._controllers[controller_id] = ctrl

        ctrl.last_seen = time.time()
        ctrl.bus_voltage_v = data.get("bus_voltage", ctrl.bus_voltage_v)
        ctrl.bus_utilization_pct = data.get("bus_utilization", ctrl.bus_utilization_pct)
        ctrl.active_nodes = data.get("connected_nodes", ctrl.active_nodes)
        ctrl.token_cycle_ms = data.get("token_cycle_ms", ctrl.token_cycle_ms)
        ctrl.uptime_s = data.get("uptime", ctrl.uptime_s)
        ctrl.total_frames = data.get("total_frames", ctrl.total_frames)
        ctrl.total_errors = data.get("total_errors", ctrl.total_errors)
        ctrl.rx_frames = data.get("rx_frames", ctrl.rx_frames)
        ctrl.tx_frames = data.get("tx_frames", ctrl.tx_frames)
        ctrl.crc_errors = data.get("crc_errors", ctrl.crc_errors)
        ctrl.framing_errors = data.get("framing_errors", ctrl.framing_errors)
        ctrl.token_timeouts = data.get("token_timeouts", ctrl.token_timeouts)

        # Per-node data inside diagnostics payload
        nodes_map = self._nodes.setdefault(controller_id, {})
        for nd in data.get("nodes", []):
            addr = nd.get("address", 0)
            node = nodes_map.get(addr)
            if node is None:
                node = NodeDiagnostics(address=addr)
                nodes_map[addr] = node
            node.last_seen = time.time()
            node.hw_id = nd.get("hw_id", node.hw_id)
            node.rx_frames = nd.get("rx_frames", node.rx_frames)
            node.tx_frames = nd.get("tx_frames", node.tx_frames)
            node.crc_errors = nd.get("crc_errors", node.crc_errors)
            node.timeouts = nd.get("timeouts", node.timeouts)
            node.uptime_s = nd.get("uptime", node.uptime_s)
            node.response_time_ms = nd.get("response_time_ms", node.response_time_ms)

        # Rolling history
        hist = self._history.setdefault(controller_id, [])
        hist.append(
            {
                "t": time.time(),
                "util": ctrl.bus_utilization_pct,
                "voltage": ctrl.bus_voltage_v,
                "nodes": ctrl.active_nodes,
                "errors": ctrl.total_errors,
                "frames": ctrl.total_frames,
            }
        )
        if len(hist) > _MAX_HISTORY:
            self._history[controller_id] = hist[-_MAX_HISTORY:]

        self._notify_listeners()

    def handle_state(
        self, controller_id: str, addr: int, state: dict[str, Any]
    ) -> None:
        """Handle ohb/{ctrl}/{addr}/state -- touch last_seen, count frames."""
        if controller_id in self._blocked:
            return
        ctrl = self._controllers.get(controller_id)
        if ctrl is None:
            ctrl = ControllerStatus(controller_id=controller_id)
            self._controllers[controller_id] = ctrl
        ctrl.total_frames += 1
        ctrl.last_seen = time.time()

        nodes_map = self._nodes.setdefault(controller_id, {})
        node = nodes_map.get(addr)
        if node is None:
            node = NodeDiagnostics(address=addr)
            nodes_map[addr] = node
        node.last_seen = time.time()
        node.rx_frames += 1

    # -- Events --------------------------------------------------------------

    def add_event(
        self,
        controller_id: str,
        event_type: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Record a bus event."""
        ev = BusEvent(
            timestamp=time.time(),
            controller_id=controller_id,
            event_type=event_type,
            message=message,
            data=data or {},
        )
        self._events.append(ev)
        if len(self._events) > 500:
            self._events = self._events[-500:]

    def get_events(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent events as dicts."""
        return [e.to_dict() for e in self._events[-limit:]]

    # -- Listeners -----------------------------------------------------------

    def add_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify_listeners(self) -> None:
        snapshot = self.get_snapshot()
        for cb in self._listeners:
            try:
                cb(snapshot)
            except Exception:
                _LOGGER.exception("Diagnostics listener error")

    # -- Background task -----------------------------------------------------

    async def start(self) -> None:
        """Periodically check for offline controllers/nodes."""
        self._running = True
        while self._running:
            await asyncio.sleep(5)

    def stop(self) -> None:
        self._running = False

    # -- Query API -----------------------------------------------------------

    def get_snapshot(self) -> dict[str, Any]:
        """Full snapshot suitable for WebSocket broadcast."""
        return {
            "controllers": {cid: c.to_dict() for cid, c in self._controllers.items()},
            "nodes": {
                cid: {addr: n.to_dict() for addr, n in sorted(nodes.items())}
                for cid, nodes in self._nodes.items()
            },
            "timestamp": time.time(),
        }

    def get_controller(self, controller_id: str) -> dict[str, Any] | None:
        """Return a single controller status as dict, or None."""
        ctrl = self._controllers.get(controller_id)
        return ctrl.to_dict() if ctrl else None

    def get_controllers_summary(self) -> list[dict[str, Any]]:
        """Return a list of all controller status dicts."""
        return [c.to_dict() for c in self._controllers.values()]

    def remove_controller(self, controller_id: str) -> bool:
        """Remove a controller and its nodes from tracking. Returns True if found."""
        found = controller_id in self._controllers
        self._controllers.pop(controller_id, None)
        self._nodes.pop(controller_id, None)
        self._history.pop(controller_id, None)
        self._blocked.add(controller_id)  # ignore future MQTT messages
        if found:
            self._notify_listeners()
        return found

    def unblock_controller(self, controller_id: str) -> None:
        """Allow a previously-blocked controller to be tracked again."""
        self._blocked.discard(controller_id)

    def get_bus_stats(self, controller_id: str | None = None) -> dict[str, Any]:
        """Aggregate bus stats, or per-controller if controller_id given."""
        if controller_id is not None:
            ctrl = self._controllers.get(controller_id)
            if ctrl is None:
                return {}
            return ctrl.to_dict()

        total_ctrl = len(self._controllers)
        online_ctrl = sum(1 for c in self._controllers.values() if c.is_online)
        total_frames = sum(c.total_frames for c in self._controllers.values())
        total_errors = sum(c.total_errors for c in self._controllers.values())
        total_nodes = sum(c.active_nodes for c in self._controllers.values())

        return {
            "total_controllers": total_ctrl,
            "online_controllers": online_ctrl,
            "total_nodes": total_nodes,
            "total_frames": total_frames,
            "total_errors": total_errors,
            "error_rate": round((total_errors / max(total_frames, 1)) * 100, 2),
        }

    def get_nodes_for_controller(self, controller_id: str) -> list[dict[str, Any]]:
        """Return node diagnostics list for a specific controller."""
        nodes = self._nodes.get(controller_id, {})
        return [n.to_dict() for n in sorted(nodes.values(), key=lambda n: n.address)]

    def get_node_stats(self, controller_id: str, address: int) -> dict[str, Any] | None:
        """Return a single node stats, or None."""
        nodes = self._nodes.get(controller_id, {})
        node = nodes.get(address)
        return node.to_dict() if node else None

    def get_history(self, controller_id: str, last_n: int = 60) -> list[dict[str, Any]]:
        """Return rolling history for a controller."""
        hist = self._history.get(controller_id, [])
        return hist[-last_n:]

    def reset_counters(self, controller_id: str | None = None) -> None:
        """Reset frame/error counters for one or all controllers."""
        targets = [controller_id] if controller_id else list(self._controllers.keys())
        for cid in targets:
            ctrl = self._controllers.get(cid)
            if ctrl:
                ctrl.total_frames = 0
                ctrl.total_errors = 0
                ctrl.rx_frames = 0
                ctrl.tx_frames = 0
                ctrl.crc_errors = 0
                ctrl.framing_errors = 0
                ctrl.token_timeouts = 0
            for node in self._nodes.get(cid, {}).values():
                node.rx_frames = 0
                node.tx_frames = 0
                node.crc_errors = 0
                node.timeouts = 0
        _LOGGER.info(
            "Diagnostics counters reset (controller=%s)", controller_id or "all"
        )
