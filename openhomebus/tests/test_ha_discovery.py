"""Tests for OpenHomeBus Dashboard — HA MQTT auto-discovery."""

import json
import pytest

from ohb_dashboard.ha_discovery import (
    discovery_payloads,
    publish_discovery,
    remove_discovery,
    _slug,
    _uid,
)
from ohb_dashboard.device_registry import DeviceEntry
from ohb_dashboard.const import DEVICE_TYPE_NODE_IO, DEVICE_TYPE_NODE_IO_2CH


class FakeMQTT:
    """Stub MQTT client that records publish calls."""

    def __init__(self):
        self.published: list[tuple[str, str, int, bool]] = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))


# ── Helper tests ──────────────────────────────────────────────────────────

class TestSlug:
    def test_simple(self):
        assert _slug("Living Room Light") == "living_room_light"

    def test_special_chars(self):
        assert _slug("sensor@#123!") == "sensor123"

    def test_dashes(self):
        assert _slug("front-door") == "front_door"


class TestUid:
    def test_basic(self):
        uid = _uid("ctrl1", 5, "di", 0)
        assert uid == "ohb_ctrl1_05_di0"


# ── Payload generation ────────────────────────────────────────────────────

class TestDiscoveryPayloads:
    def test_4ch_io_node(self, sample_device_4ch):
        entries = discovery_payloads(sample_device_4ch)
        # 4 DI (binary_sensor) + 4 DO (switch/light)
        assert len(entries) == 8

    def test_binary_sensor_di(self, sample_device_4ch):
        entries = discovery_payloads(sample_device_4ch)
        # First entry should be DI0
        di0 = entries[0]
        assert "binary_sensor" in di0["topic"]
        p = di0["payload"]
        assert p["name"] == "Input 1"
        assert "value_json.di[0]" in p["value_template"]
        assert "state_topic" in p
        assert "availability_topic" in p

    def test_device_class_on_di(self, sample_device_4ch):
        entries = discovery_payloads(sample_device_4ch)
        # DI1 has device_class="motion"
        di1 = entries[1]
        assert di1["payload"]["device_class"] == "motion"

    def test_no_device_class_when_empty(self, sample_device_4ch):
        entries = discovery_payloads(sample_device_4ch)
        # DI0 has empty device_class
        assert "device_class" not in entries[0]["payload"]

    def test_do_switch(self, sample_device_4ch):
        entries = discovery_payloads(sample_device_4ch)
        # DO0 is a switch (entries[4])
        do0 = entries[4]
        assert "/switch/" in do0["topic"]
        p = do0["payload"]
        assert "command_topic" in p
        assert "/do/0/set" in p["command_topic"]
        assert p["payload_on"] == "ON"

    def test_do_light(self, sample_device_4ch):
        entries = discovery_payloads(sample_device_4ch)
        # DO1 is a light (entries[5])
        do1 = entries[5]
        assert "/light/" in do1["topic"]

    def test_2ch_node(self, sample_device_2ch):
        entries = discovery_payloads(sample_device_2ch)
        assert len(entries) == 4  # 2 DI + 2 DO

    def test_device_block(self, sample_device_4ch):
        entries = discovery_payloads(sample_device_4ch)
        dev = entries[0]["payload"]["device"]
        assert dev["manufacturer"] == "OpenHomeBus"
        assert f"ohb_{sample_device_4ch.hw_id}" in dev["identifiers"]
        assert dev["model"] == "I/O Node (4ch)"
        assert dev["via_device"] == f"ohb_{sample_device_4ch.controller_id}"

    def test_unique_ids_are_unique(self, sample_device_4ch):
        entries = discovery_payloads(sample_device_4ch)
        uids = [e["payload"]["unique_id"] for e in entries]
        assert len(uids) == len(set(uids))

    def test_zero_io_device(self):
        dev = DeviceEntry(
            address=0x20, hw_id="SENSOR01", name="Sensor",
            controller_id="ctrl1", device_type=0x20,
            di_count=0, do_count=0,
        )
        entries = discovery_payloads(dev)
        assert entries == []


# ── Publish / Remove ─────────────────────────────────────────────────────

class TestPublish:
    def test_publish_discovery(self, sample_device_2ch):
        mqtt = FakeMQTT()
        count = publish_discovery(mqtt, sample_device_2ch)
        assert count == 4  # 2 DI + 2 DO
        assert len(mqtt.published) == 4
        for topic, payload, qos, retain in mqtt.published:
            assert "/config" in topic
            assert retain is True
            data = json.loads(payload)
            assert "unique_id" in data

    def test_remove_discovery(self, sample_device_2ch):
        mqtt = FakeMQTT()
        count = remove_discovery(mqtt, sample_device_2ch)
        assert count == 4
        for topic, payload, qos, retain in mqtt.published:
            assert payload == ""  # Empty payload = remove
            assert retain is True
