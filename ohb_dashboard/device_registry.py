"""OpenHomeBus Dashboard — Device registry (SQLite-backed).

Stores commissioned devices, their I/O naming / entity configuration,
and a bus event log for diagnostics.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .const import (
    DB_PATH,
    DEVICE_IO_DEFAULTS,
    DEVICE_TYPE_NAMES,
    NODE_STATE_OFFLINE,
    NODE_STATE_ONLINE,
)

_LOGGER = logging.getLogger(__name__)

try:
    import aiosqlite

    HAS_SQLITE = True
except ImportError:
    HAS_SQLITE = False
    _LOGGER.warning("aiosqlite not installed — device registry unavailable")


# ── Data classes ───────────────────────────────────────────────────────────


@dataclass
class DeviceEntry:
    """A single OHB device tracked by the dashboard."""

    address: int
    hw_id: str
    name: str
    controller_id: str
    device_type: int
    room: str = ""
    di_count: int = 2
    do_count: int = 2
    di_names: list[str] = field(default_factory=list)
    do_names: list[str] = field(default_factory=list)
    di_classes: list[str] = field(default_factory=list)
    do_types: list[str] = field(default_factory=list)
    fw_version: str = ""
    first_seen: str = ""
    last_seen: str = ""
    state: str = NODE_STATE_OFFLINE

    @property
    def type_label(self) -> str:
        return DEVICE_TYPE_NAMES.get(
            self.device_type, f"Unknown (0x{self.device_type:02X})"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "hw_id": self.hw_id,
            "name": self.name,
            "controller_id": self.controller_id,
            "device_type": self.device_type,
            "type_label": self.type_label,
            "room": self.room,
            "di_count": self.di_count,
            "do_count": self.do_count,
            "di_names": self.di_names,
            "do_names": self.do_names,
            "di_classes": self.di_classes,
            "do_types": self.do_types,
            "fw_version": self.fw_version,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "state": self.state,
        }


# ── Schema ─────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    address       INTEGER NOT NULL,
    controller_id TEXT    NOT NULL,
    hw_id         TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    room          TEXT    DEFAULT '',
    device_type   INTEGER NOT NULL,
    di_count      INTEGER DEFAULT 2,
    do_count      INTEGER DEFAULT 2,
    di_names      TEXT    DEFAULT '[]',
    do_names      TEXT    DEFAULT '[]',
    di_classes    TEXT    DEFAULT '[]',
    do_types      TEXT    DEFAULT '[]',
    fw_version    TEXT    DEFAULT '',
    first_seen    TEXT    NOT NULL,
    last_seen     TEXT    NOT NULL,
    state         TEXT    DEFAULT 'offline',
    PRIMARY KEY (controller_id, address),
    UNIQUE (hw_id)
);

CREATE TABLE IF NOT EXISTS bus_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    controller_id TEXT  NOT NULL,
    addr        INTEGER,
    event       TEXT    NOT NULL,
    data        TEXT
);

CREATE INDEX IF NOT EXISTS idx_bus_log_ts ON bus_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_bus_log_addr ON bus_log(addr);
"""


class DeviceRegistry:
    """SQLite-backed device registry."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = str(db_path or DB_PATH)
        self._db: Any = None

    async def start(self) -> None:
        """Open the database and create tables if needed."""
        if not HAS_SQLITE:
            _LOGGER.error("aiosqlite not available — cannot start registry")
            return
        self._db_path_obj = Path(self._db_path)
        self._db_path_obj.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        _LOGGER.info("Device registry opened: %s", self._db_path)

    async def stop(self) -> None:
        """Close the database."""
        if self._db:
            await self._db.close()
            self._db = None

    # ── Device CRUD ────────────────────────────────────────────────────

    async def list_devices(self) -> list[DeviceEntry]:
        """Return all registered devices."""
        async with self._db.execute(
            "SELECT * FROM devices ORDER BY controller_id, address"
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_entry(r) for r in rows]

    async def get_device(self, controller_id: str, address: int) -> DeviceEntry | None:
        """Look up a device by controller + address."""
        async with self._db.execute(
            "SELECT * FROM devices WHERE controller_id = ? AND address = ?",
            (controller_id, address),
        ) as cur:
            row = await cur.fetchone()
        return self._row_to_entry(row) if row else None

    async def get_device_by_hw_id(self, hw_id: str) -> DeviceEntry | None:
        """Look up a device by hardware ID."""
        async with self._db.execute(
            "SELECT * FROM devices WHERE hw_id = ?", (hw_id,)
        ) as cur:
            row = await cur.fetchone()
        return self._row_to_entry(row) if row else None

    async def add_device(
        self,
        controller_id: str,
        address: int,
        hw_id: str,
        device_type: int,
        *,
        name: str = "",
        fw_version: str = "",
    ) -> DeviceEntry:
        """Register a new device (typically from a JOIN event)."""
        now = _iso_now()
        di_default, do_default = DEVICE_IO_DEFAULTS.get(device_type, (2, 2))
        if not name:
            name = f"Node {address:02X}"

        di_names = [f"Input {i + 1}" for i in range(di_default)]
        do_names = [f"Output {i + 1}" for i in range(do_default)]
        di_classes = ["" for _ in range(di_default)]
        do_types = ["switch" for _ in range(do_default)]

        await self._db.execute(
            """INSERT OR REPLACE INTO devices
               (address, controller_id, hw_id, name, device_type,
                di_count, do_count, di_names, do_names, di_classes, do_types,
                fw_version, first_seen, last_seen, state)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                address,
                controller_id,
                hw_id,
                name,
                device_type,
                di_default,
                do_default,
                json.dumps(di_names),
                json.dumps(do_names),
                json.dumps(di_classes),
                json.dumps(do_types),
                fw_version,
                now,
                now,
                NODE_STATE_ONLINE,
            ),
        )
        await self._db.commit()
        _LOGGER.info(
            "Registered device %s addr=0x%02X on %s", hw_id, address, controller_id
        )

        return DeviceEntry(
            address=address,
            hw_id=hw_id,
            name=name,
            controller_id=controller_id,
            device_type=device_type,
            di_count=di_default,
            do_count=do_default,
            di_names=di_names,
            do_names=do_names,
            di_classes=di_classes,
            do_types=do_types,
            fw_version=fw_version,
            first_seen=now,
            last_seen=now,
            state=NODE_STATE_ONLINE,
        )

    async def update_device(
        self, controller_id: str, address: int, updates: dict[str, Any]
    ) -> DeviceEntry | None:
        """Update fields on an existing device. Returns the updated entry."""
        allowed = {
            "name",
            "room",
            "di_names",
            "do_names",
            "di_classes",
            "do_types",
            "fw_version",
            "state",
            "last_seen",
        }
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return await self.get_device(controller_id, address)

        # JSON-encode list fields
        for key in ("di_names", "do_names", "di_classes", "do_types"):
            if key in filtered and isinstance(filtered[key], list):
                filtered[key] = json.dumps(filtered[key])

        sets = ", ".join(f"{k} = ?" for k in filtered)
        vals = list(filtered.values()) + [controller_id, address]
        await self._db.execute(
            f"UPDATE devices SET {sets} WHERE controller_id = ? AND address = ?",
            vals,
        )
        await self._db.commit()
        return await self.get_device(controller_id, address)

    async def delete_device(self, controller_id: str, address: int) -> bool:
        """Remove a device from the registry."""
        cur = await self._db.execute(
            "DELETE FROM devices WHERE controller_id = ? AND address = ?",
            (controller_id, address),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def mark_online(self, controller_id: str, address: int) -> None:
        """Update device state to online with current timestamp."""
        await self._db.execute(
            "UPDATE devices SET state = ?, last_seen = ? WHERE controller_id = ? AND address = ?",
            (NODE_STATE_ONLINE, _iso_now(), controller_id, address),
        )
        await self._db.commit()

    async def mark_offline(self, controller_id: str, address: int) -> None:
        """Update device state to offline."""
        await self._db.execute(
            "UPDATE devices SET state = ? WHERE controller_id = ? AND address = ?",
            (NODE_STATE_OFFLINE, controller_id, address),
        )
        await self._db.commit()

    # ── Bus log ────────────────────────────────────────────────────────

    async def log_event(
        self,
        controller_id: str,
        event: str,
        addr: int | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Write an event to the bus log."""
        await self._db.execute(
            "INSERT INTO bus_log (timestamp, controller_id, addr, event, data) VALUES (?, ?, ?, ?, ?)",
            (
                _iso_now(),
                controller_id,
                addr,
                event,
                json.dumps(data) if data else None,
            ),
        )
        await self._db.commit()

    async def get_log(
        self, limit: int = 100, controller_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return recent bus log entries."""
        if controller_id:
            sql = (
                "SELECT * FROM bus_log WHERE controller_id = ? ORDER BY id DESC LIMIT ?"
            )
            params: tuple = (controller_id, limit)
        else:
            sql = "SELECT * FROM bus_log ORDER BY id DESC LIMIT ?"
            params = (limit,)

        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": r["id"],
                "timestamp": r["timestamp"],
                "controller_id": r["controller_id"],
                "addr": r["addr"],
                "event": r["event"],
                "data": json.loads(r["data"]) if r["data"] else None,
            }
            for r in rows
        ]

    # ── Backup ─────────────────────────────────────────────────────────

    @property
    def db_path(self) -> str:
        return self._db_path

    # ── Internal ───────────────────────────────────────────────────────

    @staticmethod
    def _row_to_entry(row: Any) -> DeviceEntry:
        return DeviceEntry(
            address=row["address"],
            hw_id=row["hw_id"],
            name=row["name"],
            controller_id=row["controller_id"],
            device_type=row["device_type"],
            room=row["room"] or "",
            di_count=row["di_count"],
            do_count=row["do_count"],
            di_names=json.loads(row["di_names"]),
            do_names=json.loads(row["do_names"]),
            di_classes=json.loads(row["di_classes"]),
            do_types=json.loads(row["do_types"]),
            fw_version=row["fw_version"] or "",
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            state=row["state"] or NODE_STATE_OFFLINE,
        )


def _iso_now() -> str:
    """Return current UTC time as ISO-8601 string."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
