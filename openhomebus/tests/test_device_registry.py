"""Tests for OpenHomeBus Dashboard — Device registry (SQLite)."""

import pytest

from ohb_dashboard.device_registry import DeviceEntry, DeviceRegistry
from ohb_dashboard.const import (
    DEVICE_TYPE_NODE_IO,
    DEVICE_TYPE_NODE_IO_2CH,
    NODE_STATE_ONLINE,
    NODE_STATE_OFFLINE,
)


@pytest.fixture
async def registry(tmp_path):
    """Provide a DeviceRegistry backed by a temp database."""
    reg = DeviceRegistry(db_path=tmp_path / "test.db")
    await reg.start()
    yield reg
    await reg.stop()


# ── DeviceEntry dataclass ─────────────────────────────────────────────────

class TestDeviceEntry:
    def test_type_label(self):
        e = DeviceEntry(
            address=1, hw_id="AA", name="n", controller_id="c",
            device_type=DEVICE_TYPE_NODE_IO,
        )
        assert e.type_label == "I/O Node (4ch)"

    def test_type_label_2ch(self):
        e = DeviceEntry(
            address=1, hw_id="BB", name="n", controller_id="c",
            device_type=DEVICE_TYPE_NODE_IO_2CH,
        )
        assert e.type_label == "I/O Node (2ch)"

    def test_type_label_unknown(self):
        e = DeviceEntry(
            address=1, hw_id="CC", name="n", controller_id="c",
            device_type=0xFF,
        )
        assert "Unknown" in e.type_label

    def test_to_dict(self):
        e = DeviceEntry(
            address=5, hw_id="AABB", name="Test",
            controller_id="ctrl1", device_type=DEVICE_TYPE_NODE_IO,
            di_names=["In1"], do_names=["Out1"],
        )
        d = e.to_dict()
        assert d["address"] == 5
        assert d["hw_id"] == "AABB"
        assert d["type_label"] == "I/O Node (4ch)"
        assert d["di_names"] == ["In1"]


# ── DeviceRegistry CRUD (async) ──────────────────────────────────────────

@pytest.mark.asyncio
class TestDeviceRegistry:
    async def test_add_and_list(self, registry):
        await registry.add_device("ctrl1", 5, "AABB", DEVICE_TYPE_NODE_IO)
        devices = await registry.list_devices()
        assert len(devices) == 1
        assert devices[0].address == 5
        assert devices[0].state == NODE_STATE_ONLINE

    async def test_add_auto_names(self, registry):
        entry = await registry.add_device("ctrl1", 0x0A, "CCDD", DEVICE_TYPE_NODE_IO)
        assert entry.di_count == 4
        assert entry.do_count == 4
        assert len(entry.di_names) == 4
        assert entry.di_names[0] == "Input 1"
        assert entry.do_types[0] == "switch"

    async def test_add_2ch_defaults(self, registry):
        entry = await registry.add_device("ctrl1", 0x0B, "EEFF", DEVICE_TYPE_NODE_IO_2CH)
        assert entry.di_count == 2
        assert entry.do_count == 2

    async def test_get_device(self, registry):
        await registry.add_device("ctrl1", 5, "AABB", DEVICE_TYPE_NODE_IO)
        dev = await registry.get_device("ctrl1", 5)
        assert dev is not None
        assert dev.hw_id == "AABB"

    async def test_get_device_not_found(self, registry):
        dev = await registry.get_device("ctrl1", 99)
        assert dev is None

    async def test_get_by_hw_id(self, registry):
        await registry.add_device("ctrl1", 5, "AABB", DEVICE_TYPE_NODE_IO)
        dev = await registry.get_device_by_hw_id("AABB")
        assert dev is not None
        assert dev.address == 5

    async def test_update_device(self, registry):
        await registry.add_device("ctrl1", 5, "AABB", DEVICE_TYPE_NODE_IO)
        updated = await registry.update_device("ctrl1", 5, {
            "name": "Renamed",
            "room": "Kitchen",
        })
        assert updated is not None
        assert updated.name == "Renamed"
        assert updated.room == "Kitchen"

    async def test_update_ignores_disallowed(self, registry):
        await registry.add_device("ctrl1", 5, "AABB", DEVICE_TYPE_NODE_IO)
        updated = await registry.update_device("ctrl1", 5, {
            "address": 99,  # Not allowed
            "name": "OK",
        })
        assert updated.name == "OK"
        assert updated.address == 5  # Unchanged

    async def test_update_nonexistent(self, registry):
        result = await registry.update_device("ctrl1", 99, {"name": "x"})
        assert result is None

    async def test_delete_device(self, registry):
        await registry.add_device("ctrl1", 5, "AABB", DEVICE_TYPE_NODE_IO)
        assert await registry.delete_device("ctrl1", 5) is True
        assert await registry.list_devices() == []

    async def test_delete_nonexistent(self, registry):
        assert await registry.delete_device("ctrl1", 99) is False

    async def test_mark_online(self, registry):
        await registry.add_device("ctrl1", 5, "AABB", DEVICE_TYPE_NODE_IO)
        await registry.mark_offline("ctrl1", 5)
        dev = await registry.get_device("ctrl1", 5)
        assert dev.state == NODE_STATE_OFFLINE

        await registry.mark_online("ctrl1", 5)
        dev = await registry.get_device("ctrl1", 5)
        assert dev.state == NODE_STATE_ONLINE

    async def test_persistence(self, tmp_path):
        db = tmp_path / "persist.db"
        reg1 = DeviceRegistry(db_path=db)
        await reg1.start()
        await reg1.add_device("ctrl1", 5, "AABB", DEVICE_TYPE_NODE_IO, name="Persistent")
        await reg1.stop()

        reg2 = DeviceRegistry(db_path=db)
        await reg2.start()
        devices = await reg2.list_devices()
        assert len(devices) == 1
        assert devices[0].name == "Persistent"
        await reg2.stop()


# ── Bus Log ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestBusLog:
    async def test_log_and_get(self, registry):
        await registry.log_event("ctrl1", "join", addr=5, data={"hw_id": "AA"})
        await registry.log_event("ctrl1", "leave", addr=5)
        log = await registry.get_log(limit=10)
        assert len(log) == 2
        # Most recent first
        assert log[0]["event"] == "leave"
        assert log[1]["event"] == "join"
        assert log[1]["data"]["hw_id"] == "AA"

    async def test_log_filter_by_controller(self, registry):
        await registry.log_event("ctrl1", "join", addr=5)
        await registry.log_event("ctrl2", "join", addr=6)
        log = await registry.get_log(controller_id="ctrl1")
        assert len(log) == 1
        assert log[0]["controller_id"] == "ctrl1"
