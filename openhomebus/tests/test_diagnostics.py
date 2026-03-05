"""Tests for OpenHomeBus Dashboard — per-controller diagnostics module."""

import time

import pytest

from ohb_dashboard.diagnostics import (
    ControllerStatus,
    BusEvent,
    DiagnosticsManager,
    NodeDiagnostics,
    _format_uptime,
    _health_level,
)


# ── Helpers ────────────────────────────────────────────────────────────────

class TestFormatUptime:
    def test_zero(self):
        assert _format_uptime(0) == "—"

    def test_seconds(self):
        assert _format_uptime(45) == "45s"

    def test_minutes(self):
        assert _format_uptime(125) == "2m"

    def test_hours(self):
        assert _format_uptime(7200) == "2h"

    def test_days(self):
        assert _format_uptime(90061) == "1d 1h 1m"


class TestHealthLevel:
    def test_good(self):
        assert _health_level(10, 50, 80) == "good"

    def test_warning(self):
        assert _health_level(60, 50, 80) == "warning"

    def test_critical(self):
        assert _health_level(90, 50, 80) == "critical"


# ── NodeDiagnostics ───────────────────────────────────────────────────────

class TestNodeDiagnostics:
    def test_defaults(self):
        n = NodeDiagnostics()
        assert n.address == 0
        assert n.rx_frames == 0
        assert n.crc_errors == 0
        assert n.response_time_ms == 0.0

    def test_is_online_fresh(self):
        n = NodeDiagnostics(last_seen=time.time())
        assert n.is_online is True

    def test_is_online_stale(self):
        n = NodeDiagnostics(last_seen=time.time() - 60)
        assert n.is_online is False

    def test_error_rate(self):
        n = NodeDiagnostics(rx_frames=80, tx_frames=20, crc_errors=5)
        assert n.error_rate == 5.0

    def test_error_rate_zero_frames(self):
        n = NodeDiagnostics()
        assert n.error_rate == 0.0

    def test_to_dict(self):
        n = NodeDiagnostics(address=3, hw_id="AABB", rx_frames=10, tx_frames=5,
                            last_seen=time.time(), response_time_ms=2.5)
        d = n.to_dict()
        assert d["address"] == 3
        assert d["rx_frames"] == 10
        assert d["tx_frames"] == 5
        assert d["response_time_ms"] == 2.5
        assert "online" in d
        assert "error_rate" in d


# ── ControllerStatus ──────────────────────────────────────────────────────

class TestControllerStatus:
    def test_defaults(self):
        c = ControllerStatus(controller_id="ctrl1")
        assert c.controller_id == "ctrl1"
        assert c.online is False
        assert c.uptime_s == 0

    def test_is_online(self):
        c = ControllerStatus(controller_id="c1", last_seen=time.time())
        assert c.is_online is True

    def test_is_offline_stale(self):
        c = ControllerStatus(controller_id="c1", last_seen=time.time() - 120)
        assert c.is_online is False

    def test_error_rate(self):
        c = ControllerStatus(total_frames=200, total_errors=4)
        assert c.error_rate == 2.0

    def test_heap_usage_pct(self):
        c = ControllerStatus(free_heap=100_000, total_heap=300_000)
        assert abs(c.heap_usage_pct - 66.7) < 0.1

    def test_wifi_quality(self):
        c1 = ControllerStatus(wifi_rssi=-40)
        assert c1._rssi_to_quality() == "excellent"
        c2 = ControllerStatus(wifi_rssi=-55)
        assert c2._rssi_to_quality() == "good"
        c3 = ControllerStatus(wifi_rssi=-65)
        assert c3._rssi_to_quality() == "fair"
        c4 = ControllerStatus(wifi_rssi=-80)
        assert c4._rssi_to_quality() == "poor"

    def test_to_dict(self):
        c = ControllerStatus(controller_id="ctrl1", uptime_s=3600,
                             fw_version="1.0", bus_voltage_v=4.9,
                             last_seen=time.time())
        d = c.to_dict()
        assert d["controller_id"] == "ctrl1"
        assert d["uptime_str"] == "1h"
        assert d["fw_version"] == "1.0"
        assert d["bus_voltage"] == 4.9
        assert "online" in d
        assert "wifi_quality" in d
        assert "heap_usage_pct" in d


# ── BusEvent ──────────────────────────────────────────────────────────────

class TestBusEvent:
    def test_to_dict(self):
        ev = BusEvent(timestamp=time.time(), controller_id="c1",
                      event_type="join", message="Node joined")
        d = ev.to_dict()
        assert d["event_type"] == "join"
        assert d["message"] == "Node joined"
        assert "ago" in d


# ── DiagnosticsManager ───────────────────────────────────────────────────

class TestDiagnosticsManager:
    def test_initial_snapshot(self):
        dm = DiagnosticsManager()
        snap = dm.get_snapshot()
        assert "controllers" in snap
        assert "nodes" in snap
        assert snap["controllers"] == {}
        assert snap["nodes"] == {}

    def test_handle_controller_status(self):
        dm = DiagnosticsManager()
        dm.handle_controller_status("ctrl1", {
            "online": True, "uptime": 7200, "fw_version": "1.0",
            "wifi_rssi": -55, "free_heap": 150000, "total_heap": 300000,
            "cpu_temp": 42.5, "restart_count": 3,
        })
        c = dm.get_controller("ctrl1")
        assert c is not None
        assert c["uptime_s"] == 7200
        assert c["fw_version"] == "1.0"
        assert c["wifi_rssi"] == -55
        assert c["cpu_temp_c"] == 42.5
        assert c["restart_count"] == 3

    def test_handle_diagnostics(self):
        dm = DiagnosticsManager()
        dm.handle_diagnostics("ctrl1", {
            "bus_voltage": 4.85,
            "bus_utilization": 23.5,
            "connected_nodes": 4,
            "token_cycle_ms": 12.3,
            "uptime": 7200,
            "total_frames": 10000,
            "total_errors": 15,
        })
        c = dm.get_controller("ctrl1")
        assert c is not None
        assert c["bus_voltage"] == 4.85
        assert c["bus_utilization"] == 23.5
        assert c["total_frames"] == 10000

    def test_handle_diagnostics_with_nodes(self):
        dm = DiagnosticsManager()
        dm.handle_diagnostics("ctrl1", {
            "bus_voltage": 5.0,
            "nodes": [
                {
                    "address": 5,
                    "hw_id": "AABB",
                    "rx_frames": 100,
                    "tx_frames": 50,
                    "crc_errors": 2,
                    "uptime": 3600,
                    "response_time_ms": 1.5,
                },
            ],
        })
        nodes = dm.get_nodes_for_controller("ctrl1")
        assert len(nodes) == 1
        assert nodes[0]["rx_frames"] == 100
        assert nodes[0]["crc_errors"] == 2
        assert nodes[0]["uptime_s"] == 3600
        assert nodes[0]["response_time_ms"] == 1.5

    def test_get_node_stats(self):
        dm = DiagnosticsManager()
        dm.handle_diagnostics("ctrl1", {
            "nodes": [{"address": 5, "rx_frames": 42}],
        })
        node = dm.get_node_stats("ctrl1", 5)
        assert node is not None
        assert node["rx_frames"] == 42

    def test_get_node_stats_none(self):
        dm = DiagnosticsManager()
        assert dm.get_node_stats("ctrl1", 42) is None

    def test_handle_state_tracks_frames(self):
        dm = DiagnosticsManager()
        dm.handle_state("ctrl1", 0x0A, {"di": [True, False], "do": [False, True]})
        dm.handle_state("ctrl1", 0x0A, {"di": [True, True], "do": [False, True]})
        node = dm.get_node_stats("ctrl1", 0x0A)
        assert node is not None
        assert node["rx_frames"] == 2

    def test_get_controllers_summary(self):
        dm = DiagnosticsManager()
        dm.handle_controller_status("c1", {"online": True})
        dm.handle_controller_status("c2", {"online": True})
        summary = dm.get_controllers_summary()
        assert len(summary) == 2

    def test_get_bus_stats_aggregate(self):
        dm = DiagnosticsManager()
        dm.handle_controller_status("c1", {"online": True})
        dm.handle_controller_status("c2", {"online": True})
        stats = dm.get_bus_stats()
        assert stats["total_controllers"] == 2
        assert stats["online_controllers"] == 2

    def test_get_bus_stats_single(self):
        dm = DiagnosticsManager()
        dm.handle_controller_status("c1", {"online": True, "fw_version": "1.0"})
        stats = dm.get_bus_stats("c1")
        assert stats["fw_version"] == "1.0"

    def test_bus_history(self):
        dm = DiagnosticsManager()
        for i in range(5):
            dm.handle_diagnostics("ctrl1", {
                "bus_voltage": 5.0,
                "bus_utilization": float(i),
            })
        history = dm.get_history("ctrl1", last_n=3)
        assert len(history) == 3

    def test_history_limit(self):
        dm = DiagnosticsManager()
        for i in range(310):
            dm.handle_diagnostics("ctrl1", {"bus_voltage": 5.0})
        assert len(dm._history["ctrl1"]) == 300

    def test_add_event(self):
        dm = DiagnosticsManager()
        dm.add_event("c1", "join", "Node joined", {"address": 5})
        events = dm.get_events(10)
        assert len(events) == 1
        assert events[0]["event_type"] == "join"
        assert events[0]["message"] == "Node joined"

    def test_listener_pattern(self):
        dm = DiagnosticsManager()
        received = []
        dm.add_listener(lambda snap: received.append(snap))
        dm.handle_diagnostics("ctrl1", {"bus_voltage": 5.0})
        assert len(received) == 1
        assert "controllers" in received[0]

    def test_remove_listener(self):
        dm = DiagnosticsManager()
        calls = []
        cb = lambda snap: calls.append(1)
        dm.add_listener(cb)
        dm.remove_listener(cb)
        dm.handle_diagnostics("ctrl1", {"bus_voltage": 5.0})
        assert calls == []

    def test_reset_counters_single(self):
        dm = DiagnosticsManager()
        dm.handle_state("ctrl1", 5, {"di": [True]})
        dm._controllers["ctrl1"].total_errors = 3
        dm.reset_counters("ctrl1")
        c = dm.get_controller("ctrl1")
        assert c["total_frames"] == 0
        assert c["total_errors"] == 0
        node = dm.get_node_stats("ctrl1", 5)
        assert node["rx_frames"] == 0

    def test_reset_counters_all(self):
        dm = DiagnosticsManager()
        dm.handle_state("ctrl1", 5, {"di": [True]})
        dm.handle_state("ctrl2", 3, {"di": [True]})
        dm.reset_counters()
        for cid in ("ctrl1", "ctrl2"):
            c = dm.get_controller(cid)
            assert c["total_frames"] == 0

    def test_get_nodes_for_controller_empty(self):
        dm = DiagnosticsManager()
        assert dm.get_nodes_for_controller("nonexistent") == []

    def test_stop(self):
        dm = DiagnosticsManager()
        dm.stop()
        assert dm._running is False
