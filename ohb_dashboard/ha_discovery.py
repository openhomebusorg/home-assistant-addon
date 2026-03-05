"""OpenHomeBus Dashboard — Home Assistant MQTT auto-discovery.

Generates discovery payloads from the SQLite device registry
(DeviceEntry) and publishes them via the MQTTClient.

Topic mapping:
  DI channel i  → binary_sensor  state from ``ohb/{ctrl}/{addr}/state``
  DO channel i  → switch / light  state + command via
                  ``ohb/{ctrl}/{addr}/do/{i}/set``
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TYPE_CHECKING

from .const import (
    HA_DISCOVERY_PREFIX,
    MQTT_QOS_COMMAND,
    MQTT_TOPIC_PREFIX,
)

_LOGGER = logging.getLogger(__name__)

# ── Type imports (avoid circular at runtime) ──────────────────────────────

if TYPE_CHECKING:
    from .device_registry import DeviceEntry
    from .mqtt_client import MQTTClient


# ── Public API ─────────────────────────────────────────────────────────────


def discovery_payloads(device: "DeviceEntry") -> list[dict[str, Any]]:
    """Build HA MQTT discovery entries for a single DeviceEntry.

    Returns a list of ``{"topic": ..., "payload": ...}`` dicts.
    """
    entries: list[dict[str, Any]] = []
    dev_block = _device_block(device)
    ctrl = device.controller_id
    addr = f"{device.address:02X}"
    state_topic = f"{MQTT_TOPIC_PREFIX}/{ctrl}/{addr}/state"
    avail_topic = f"{MQTT_TOPIC_PREFIX}/{ctrl}/{addr}/availability"

    # ── Digital inputs → binary_sensor ────────────────────────────────
    for i in range(device.di_count):
        name = device.di_names[i] if i < len(device.di_names) else f"Input {i + 1}"
        uid = _uid(ctrl, device.address, "di", i)
        payload: dict[str, Any] = {
            "name": name,
            "unique_id": uid,
            "state_topic": state_topic,
            "value_template": f"{{{{ 'ON' if value_json.di[{i}] else 'OFF' }}}}",
            "payload_on": "ON",
            "payload_off": "OFF",
            "availability_topic": avail_topic,
            "device": dev_block,
        }
        dclass = device.di_classes[i] if i < len(device.di_classes) else ""
        if dclass:
            payload["device_class"] = dclass

        entries.append(
            {
                "topic": f"{HA_DISCOVERY_PREFIX}/binary_sensor/{uid}/config",
                "payload": payload,
            }
        )

    # ── Digital outputs → switch or light ─────────────────────────────
    for i in range(device.do_count):
        name = device.do_names[i] if i < len(device.do_names) else f"Output {i + 1}"
        do_type = device.do_types[i] if i < len(device.do_types) else "switch"
        component = do_type if do_type in ("switch", "light") else "switch"
        uid = _uid(ctrl, device.address, "do", i)

        payload = {
            "name": name,
            "unique_id": uid,
            "state_topic": state_topic,
            "value_template": f"{{{{ 'ON' if value_json.do[{i}] else 'OFF' }}}}",
            "command_topic": f"{MQTT_TOPIC_PREFIX}/{ctrl}/{addr}/do/{i}/set",
            "payload_on": "ON",
            "payload_off": "OFF",
            "availability_topic": avail_topic,
            "device": dev_block,
        }
        entries.append(
            {
                "topic": f"{HA_DISCOVERY_PREFIX}/{component}/{uid}/config",
                "payload": payload,
            }
        )

    if not entries:
        _LOGGER.debug("No HA entities for device addr=%s", addr)

    return entries


def publish_discovery(mqtt: "MQTTClient", device: "DeviceEntry") -> int:
    """Publish all HA discovery payloads for *device*. Returns count."""
    entries = discovery_payloads(device)
    for e in entries:
        mqtt.publish(
            e["topic"], json.dumps(e["payload"]), qos=MQTT_QOS_COMMAND, retain=True
        )
    _LOGGER.info(
        "Published %d HA discovery entries for %s (0x%02X)",
        len(entries),
        device.name,
        device.address,
    )
    return len(entries)


def remove_discovery(mqtt: "MQTTClient", device: "DeviceEntry") -> int:
    """Remove all HA discovery payloads (publish empty retained). Returns count."""
    entries = discovery_payloads(device)
    for e in entries:
        mqtt.publish(e["topic"], "", qos=MQTT_QOS_COMMAND, retain=True)
    _LOGGER.info(
        "Removed %d HA discovery entries for %s (0x%02X)",
        len(entries),
        device.name,
        device.address,
    )
    return len(entries)


# ── Helpers ────────────────────────────────────────────────────────────────


def _device_block(device: "DeviceEntry") -> dict[str, Any]:
    """Build the ``device`` block required by HA discovery."""
    return {
        "identifiers": [f"ohb_{device.hw_id}"],
        "name": device.name,
        "model": device.type_label,
        "manufacturer": "OpenHomeBus",
        "sw_version": device.fw_version or "0.0.0",
        "hw_version": device.hw_id,
        "via_device": f"ohb_{device.controller_id}",
    }


def _uid(controller_id: str, address: int, kind: str, channel: int) -> str:
    """Generate a globally unique entity ID."""
    return f"ohb_{_slug(controller_id)}_{address:02x}_{kind}{channel}"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_\- ]", "", str(text))
    return re.sub(r"[\s\-]+", "_", s).strip("_").lower()


# ── Controller-level HA discovery removal ─────────────────────────────────

# Must match the object_ids used by mqtt_bridge_publish_ha_discovery() in
# the firmware (mqtt_bridge.c).  Topic pattern:
#   homeassistant/sensor/ohb_{ctrl_id}_{object_id}/config
_CONTROLLER_SENSOR_IDS = (
    "uptime",
    "wifi_rssi",
    "free_heap",
    "active_nodes",
    "total_frames",
    "crc_errors",
    "bus_util",
    "token_cycle",
    "connected_nodes",
)


def remove_controller_discovery(mqtt: "MQTTClient", controller_id: str) -> int:
    """Remove all HA discovery entries the controller firmware published.

    Publishes an empty retained message to each topic so HA removes
    the entities.
    """
    slug = _slug(controller_id)
    count = 0
    for obj_id in _CONTROLLER_SENSOR_IDS:
        topic = f"{HA_DISCOVERY_PREFIX}/sensor/ohb_{slug}_{obj_id}/config"
        mqtt.publish(topic, "", qos=MQTT_QOS_COMMAND, retain=True)
        count += 1
    _LOGGER.info(
        "Removed %d HA discovery entries for controller %s",
        count,
        controller_id,
    )
    return count
