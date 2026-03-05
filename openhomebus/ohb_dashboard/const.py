"""OpenHomeBus Dashboard — Constants."""

import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("OHB_DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "ohb.db"

# ── Defaults ───────────────────────────────────────────────────────────────
DEFAULT_PORT = 6052
DASHBOARD_TITLE = "OpenHomeBus"

# ── MQTT ───────────────────────────────────────────────────────────────────
MQTT_DEFAULT_PORT = 1883
MQTT_TOPIC_PREFIX = "ohb"
HA_DISCOVERY_PREFIX = "homeassistant"
MQTT_QOS_STATE = 0
MQTT_QOS_COMMAND = 1

# ── Device types (from ohb_types.h) ────────────────────────────────────────
DEVICE_TYPE_CONTROLLER = 0x01
DEVICE_TYPE_NODE_IO = 0x10
DEVICE_TYPE_NODE_IO_2CH = 0x11
DEVICE_TYPE_NODE_SENSOR = 0x20
DEVICE_TYPE_NODE_DIMMER = 0x30

DEVICE_TYPE_NAMES: dict[int, str] = {
    DEVICE_TYPE_CONTROLLER: "Controller",
    DEVICE_TYPE_NODE_IO: "I/O Node (4ch)",
    DEVICE_TYPE_NODE_IO_2CH: "I/O Node (2ch)",
    DEVICE_TYPE_NODE_SENSOR: "Sensor Node",
    DEVICE_TYPE_NODE_DIMMER: "Dimmer Node",
}

# Default I/O channel counts per device type
DEVICE_IO_DEFAULTS: dict[int, tuple[int, int]] = {
    # (di_count, do_count)
    DEVICE_TYPE_CONTROLLER: (0, 0),
    DEVICE_TYPE_NODE_IO: (4, 4),
    DEVICE_TYPE_NODE_IO_2CH: (2, 2),
    DEVICE_TYPE_NODE_SENSOR: (0, 0),
    DEVICE_TYPE_NODE_DIMMER: (0, 4),
}

# ── Node states ────────────────────────────────────────────────────────────
NODE_STATE_ONLINE = "online"
NODE_STATE_OFFLINE = "offline"
NODE_STATE_JOINING = "joining"

# ── Log levels ─────────────────────────────────────────────────────────────
LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]
