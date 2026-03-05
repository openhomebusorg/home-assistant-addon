"""OpenHomeBus Dashboard — MQTT client.

Listens to controller MQTT topics for device state and events.
Publishes HA auto-discovery payloads on behalf of the user.
Does NOT relay I/O commands — the controller handles that directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from .const import (
    HA_DISCOVERY_PREFIX,
    MQTT_QOS_COMMAND,
    MQTT_QOS_STATE,
    MQTT_TOPIC_PREFIX,
)

_LOGGER = logging.getLogger(__name__)

try:
    import paho.mqtt.client as paho_mqtt

    HAS_MQTT = True
    # paho-mqtt v2 requires explicit callback API version
    try:
        from paho.mqtt.client import CallbackAPIVersion

        _PAHO_V2 = True
    except ImportError:
        _PAHO_V2 = False
except ImportError:
    HAS_MQTT = False
    _PAHO_V2 = False
    _LOGGER.warning("paho-mqtt not installed — MQTT features disabled")


class MQTTClient:
    """MQTT client for the OHB add-on.

    Responsibilities:
      - Subscribe to controller status / node events / state / diagnostics
      - Publish HA auto-discovery payloads when user commissions a device
      - Forward MQTT events to registered callbacks for the dashboard
    """

    def __init__(
        self,
        broker: str = "localhost",
        port: int = 1883,
        username: str = "",
        password: str = "",
    ) -> None:
        self._broker = broker
        self._port = port
        self._username = username
        self._password = password
        self._client: Any = None
        self._connected = False
        self._loop: asyncio.AbstractEventLoop | None = None

        # Callback registrations
        self._on_node_event_cbs: list[Callable] = []
        self._on_state_update_cbs: list[Callable] = []
        self._on_controller_status_cbs: list[Callable] = []
        self._on_diagnostics_cbs: list[Callable] = []
        self._on_availability_cbs: list[Callable] = []

    # ── Connection ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Connect to the MQTT broker."""
        if not HAS_MQTT:
            _LOGGER.error("Cannot connect — paho-mqtt not installed")
            return

        self._loop = asyncio.get_running_loop()
        client_kwargs = {
            "client_id": f"ohb-addon-{id(self) & 0xFFFF:04x}",
            "protocol": paho_mqtt.MQTTv311,
        }
        if _PAHO_V2:
            client_kwargs["callback_api_version"] = CallbackAPIVersion.VERSION1
        self._client = paho_mqtt.Client(**client_kwargs)
        if self._username:
            self._client.username_pw_set(self._username, self._password)

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        try:
            await self._loop.run_in_executor(
                None, self._client.connect, self._broker, self._port, 60
            )
            self._client.loop_start()
            _LOGGER.info("MQTT connecting to %s:%d", self._broker, self._port)
        except Exception:
            _LOGGER.exception("MQTT connection failed")

    async def stop(self) -> None:
        """Disconnect from the MQTT broker."""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._connected = False
            _LOGGER.info("MQTT disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Callback registration ──────────────────────────────────────────

    def on_node_event(self, callback: Callable) -> None:
        """Register callback for node join/leave events.

        Callback signature: (controller_id: str, event: dict) -> None
        """
        self._on_node_event_cbs.append(callback)

    def on_state_update(self, callback: Callable) -> None:
        """Register callback for node I/O state changes.

        Callback signature: (controller_id: str, addr: int, state: dict) -> None
        """
        self._on_state_update_cbs.append(callback)

    def on_controller_status(self, callback: Callable) -> None:
        """Register callback for controller status updates.

        Callback signature: (controller_id: str, status: dict) -> None
        """
        self._on_controller_status_cbs.append(callback)

    def on_diagnostics(self, callback: Callable) -> None:
        """Register callback for bus diagnostics.

        Callback signature: (controller_id: str, diagnostics: dict) -> None
        """
        self._on_diagnostics_cbs.append(callback)

    def on_availability(self, callback: Callable) -> None:
        """Register callback for node availability changes.

        Callback signature: (controller_id: str, addr: int, available: bool) -> None
        """
        self._on_availability_cbs.append(callback)

    # ── Publishing ─────────────────────────────────────────────────────

    def publish(
        self, topic: str, payload: str | bytes, *, qos: int = 0, retain: bool = False
    ) -> None:
        """Publish a message to the MQTT broker."""
        if self._client and self._connected:
            self._client.publish(topic, payload, qos=qos, retain=retain)

    def publish_ha_discovery(
        self, component: str, unique_id: str, payload: dict[str, Any]
    ) -> None:
        """Publish a single HA auto-discovery config message."""
        topic = f"{HA_DISCOVERY_PREFIX}/{component}/{unique_id}/config"
        self.publish(topic, json.dumps(payload), qos=MQTT_QOS_COMMAND, retain=True)

    def remove_ha_discovery(self, component: str, unique_id: str) -> None:
        """Remove a device from HA by publishing an empty discovery payload."""
        topic = f"{HA_DISCOVERY_PREFIX}/{component}/{unique_id}/config"
        self.publish(topic, "", qos=MQTT_QOS_COMMAND, retain=True)

    # ── Internal handlers ──────────────────────────────────────────────

    def _on_connect(self, client: Any, userdata: Any, flags: Any, rc: int) -> None:
        if rc != 0:
            _LOGGER.error("MQTT connection refused (rc=%d)", rc)
            self._connected = False
            return
        self._connected = True
        _LOGGER.info("MQTT connected successfully")

        # Subscribe to all OHB topics
        prefix = MQTT_TOPIC_PREFIX
        client.subscribe(f"{prefix}/+/status", qos=MQTT_QOS_STATE)
        client.subscribe(f"{prefix}/+/event", qos=MQTT_QOS_COMMAND)
        client.subscribe(f"{prefix}/+/+/state", qos=MQTT_QOS_STATE)
        client.subscribe(f"{prefix}/+/+/availability", qos=MQTT_QOS_STATE)
        client.subscribe(f"{prefix}/+/diagnostics", qos=MQTT_QOS_STATE)

    def _on_disconnect(self, client: Any, userdata: Any, rc: int) -> None:
        self._connected = False
        if rc != 0:
            _LOGGER.warning("MQTT disconnected unexpectedly (rc=%d)", rc)
        else:
            _LOGGER.info("MQTT disconnected cleanly")

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        """Route incoming MQTT messages to the appropriate callbacks."""
        topic = msg.topic
        parts = topic.split("/")

        # All OHB topics start with "ohb/{controller_id}/..."
        if len(parts) < 3 or parts[0] != MQTT_TOPIC_PREFIX:
            return

        controller_id = parts[1]

        try:
            # ohb/{ctrl}/status
            if len(parts) == 3 and parts[2] == "status":
                data = json.loads(msg.payload)
                self._fire(self._on_controller_status_cbs, controller_id, data)

            # ohb/{ctrl}/event
            elif len(parts) == 3 and parts[2] == "event":
                data = json.loads(msg.payload)
                self._fire(self._on_node_event_cbs, controller_id, data)

            # ohb/{ctrl}/diagnostics
            elif len(parts) == 3 and parts[2] == "diagnostics":
                data = json.loads(msg.payload)
                self._fire(self._on_diagnostics_cbs, controller_id, data)

            # ohb/{ctrl}/{addr}/state
            elif len(parts) == 4 and parts[3] == "state":
                addr = int(parts[2], 16)
                data = json.loads(msg.payload)
                self._fire(self._on_state_update_cbs, controller_id, addr, data)

            # ohb/{ctrl}/{addr}/availability
            elif len(parts) == 4 and parts[3] == "availability":
                addr = int(parts[2], 16)
                available = msg.payload.decode().strip().lower() == "online"
                self._fire(self._on_availability_cbs, controller_id, addr, available)

        except (json.JSONDecodeError, ValueError, IndexError):
            _LOGGER.debug("Failed to parse MQTT message on %s", topic)

    def _fire(self, callbacks: list[Callable], *args: Any) -> None:
        """Invoke all registered callbacks on the main event loop (thread-safe)."""
        loop = self._loop
        for cb in callbacks:
            try:
                if loop and loop.is_running():
                    loop.call_soon_threadsafe(cb, *args)
                else:
                    cb(*args)
            except Exception:
                _LOGGER.exception("Error in MQTT callback")
