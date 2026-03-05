# OpenHomeBus Add-on — Rewrite Concept

> **Status**: Draft — March 2026  
> **Goal**: Strip out assumptions that don't match reality, bridge the gap between the working firmware (L1+L2 HDLC/RS-485) and Home Assistant, and make the add-on genuinely useful **today** while keeping a clear upgrade path.

---

## 1. Why a Rewrite?

The current add-on was designed around an architecture that doesn't exist yet: nodes running a full MQTT stack over IPv4/UDP on the RS-485 bus. In reality, the firmware speaks **raw HDLC frames** — the controller polls nodes via token-passing and receives binary I/O state. There is no MQTT broker on the controller, no Ethernet driver, no IP layer on the bus.

This means the add-on has a fully coded MQTT discovery system, OTA implementation, and diagnostics pipeline — all waiting for data that will never arrive under the current firmware.

**The core fix**: the controller uses its **W5500 Ethernet** interface to connect directly to the network and speak MQTT to Mosquitto. The add-on becomes a **management and configuration UI** — not a serial bridge — that handles device commissioning, HA discovery, and diagnostics.

---

## 2. New Architecture

The OHB Controller has **100 Mbit Ethernet** (W5500 over SPI) and connects directly to the local network. It runs an embedded MQTT client that connects to HA's Mosquitto broker, translating between HDLC frames on the RS-485 bus and MQTT messages on the network. The add-on is the **management and configuration layer** — it doesn't need to sit in the data path.

```
┌──────────────────────────────────────────────────────────────┐
│                    Local Network (Ethernet)                   │
│                                                              │
│  ┌─────────────┐     MQTT      ┌──────────────────────────┐ │
│  │  Mosquitto   │◄────────────►│  OHB Controller          │ │
│  │  Broker      │              │  (ESP32-S3 + W5500)      │ │
│  └──────┬───────┘              │                          │ │
│         │                      │  Ethernet ↔ RS-485 bridge│ │
│         │                      │  Embedded MQTT client    │ │
│         │ HA auto-             │  Bus master / addressing │ │
│         │ discovery            └────────────┬─────────────┘ │
│         ▼                                   │ RS-485 bus    │
│  ┌─────────────┐              ┌─────────────┴─────────┐     │
│  │ HA Entities  │              ▼          ▼          ▼      │
│  │ (switches,   │        ┌─────────┐ ┌─────────┐ ┌─────────┐
│  │  sensors,    │        │ Node 01 │ │ Node 02 │ │ Node 03 │
│  │  lights)     │        │ 4DI/4DO │ │ 4DI/4DO │ │ Sensor  │
│  └─────────────┘        └─────────┘ └─────────┘ └─────────┘
│                                                              │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  OHB Add-on (Home Assistant)                         │    │
│  │  • Commissioning UI (name, room, I/O labels)         │    │
│  │  • Device registry (SQLite)                          │    │
│  │  • HA discovery publisher                            │    │
│  │  • Diagnostics dashboard                             │    │
│  │  • Controller management (config, firmware updates)  │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

**The controller IS the bridge.** It:

1. Connects to the LAN via Ethernet (W5500, 100 Mbit, RJ-45)
2. Runs an MQTT client that connects to HA's Mosquitto broker
3. Polls nodes on the RS-485 bus via token-passing (HDLC frames, 500 kbit/s)
4. Translates I/O state changes into MQTT publishes
5. Receives MQTT commands and forwards them as OUTPUT_CMD frames to nodes

**The add-on is the management layer.** It:

1. Discovers controllers on the network (via MQTT birth messages)
2. Provides a dashboard UI for commissioning, naming, and configuring devices
3. Publishes HA auto-discovery messages so entities appear in HA
4. Monitors bus health and diagnostics via MQTT telemetry topics
5. Manages the device registry (names, rooms, I/O labels, entity types)

The controller handles the real-time data path. The add-on handles configuration and UX. Clean separation of concerns.

---

## 3. What Gets Removed

| Component                                 | Why                                                                                                                                                                                                                       |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `serial_port.py` (port listing/detection) | The controller communicates over Ethernet/MQTT, not USB serial. No serial bridge needed.                                                                                                                                  |
| `platformio_api.py`                       | PlatformIO builds don't belong in a runtime add-on. Firmware building is a developer activity — use the CLI or a separate GitHub Actions workflow                                                                         |
| `yaml_config.py` + templates              | The ESPHome-style "compile your own firmware" workflow doesn't apply. OHB nodes run a **standard firmware** — configuration is done at the bus level (address assignment, I/O naming, entity mapping), not by recompiling |
| `ota_password` option                     | OTA will be reimplemented properly when the firmware supports it                                                                                                                                                          |
| `serial_port` option                      | Controller uses Ethernet — no serial port config needed                                                                                                                                                                   |
| PlatformIO / build dependencies           | Removes ~800 MB from the Docker image                                                                                                                                                                                     |
| SSE build streaming                       | No builds to stream                                                                                                                                                                                                       |

---

## 4. What Gets Added

### 4.1 Controller MQTT Topics (firmware-side, not add-on code)

The controller firmware handles the real-time data path. It connects to Mosquitto over Ethernet and uses this topic structure:

**Published by the controller:**

```
ohb/{controller_id}/status                → {"online": true, "nodes": 3, "uptime": 86400, "bus_voltage": 47.8, "fw_version": "0.3.0"}
ohb/{controller_id}/{addr}/state          → {"di": [true, false, false, false], "do": [true, false, false, false]}
ohb/{controller_id}/{addr}/availability   → "online" / "offline"
ohb/{controller_id}/event                 → {"type": "join", "addr": 3, "hw_id": "AA:BB:CC:DD:EE:FF", "dev_type": 17}
ohb/{controller_id}/diagnostics           → {"bus_util": 12.5, "crc_errors": 0, "timeouts": 1, ...}
```

**Subscribed by the controller:**

```
ohb/{controller_id}/{addr}/do/{channel}/set   → "ON" / "OFF"   (relay commands from HA)
ohb/{controller_id}/cmd                       → {"action": "reboot"} / {"action": "poll_all"}
```

The controller translates MQTT commands into HDLC OUTPUT_CMD frames on the RS-485 bus and translates TOKEN_RESPONSE I/O state back into MQTT publishes. It also publishes JOIN events when new nodes appear, and marks nodes offline when they stop responding to token polls.

### 4.2 MQTT Client (`mqtt_client.py`)

The add-on's MQTT module is a **listener and configuration publisher**, not a data bridge:

- Connects to HA's Mosquitto (auto-discovered or configured)
- **Subscribes** to `ohb/+/event` to detect new nodes joining the bus
- **Subscribes** to `ohb/+/+/state` and `ohb/+/+/availability` to track device state for the dashboard
- **Subscribes** to `ohb/+/status` to monitor controller health
- **Subscribes** to `ohb/+/diagnostics` for bus health telemetry
- **Publishes** HA auto-discovery messages (`homeassistant/...`) when a user commissions a node in the dashboard
- **Publishes** HA discovery removal messages when a user deletes a device
- Does **not** relay I/O commands — that's the controller's job

```python
class MQTTClient:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def on_node_event(self, callback: Callable) -> None: ...
    def on_state_update(self, callback: Callable) -> None: ...
    def on_controller_status(self, callback: Callable) -> None: ...
    async def publish_ha_discovery(self, device: DeviceEntry) -> None: ...
    async def remove_ha_discovery(self, device: DeviceEntry) -> None: ...
```

### 4.3 Device Registry (`device_registry.py`)

Replaces the JSON-file `DeviceManager` with SQLite:

```sql
CREATE TABLE devices (
    address     INTEGER PRIMARY KEY,    -- bus address 0x01–0xFD
    hw_id       TEXT UNIQUE NOT NULL,   -- 6-byte hardware ID (hex)
    name        TEXT NOT NULL,          -- user-assigned name
    room        TEXT,                   -- room/zone assignment
    type        INTEGER NOT NULL,       -- device type code
    di_count    INTEGER DEFAULT 2,      -- number of digital inputs
    do_count    INTEGER DEFAULT 2,      -- number of digital outputs
    di_names    TEXT,                   -- JSON array of input names
    do_names    TEXT,                   -- JSON array of output names
    di_classes  TEXT,                   -- JSON array of HA device_classes
    do_types    TEXT,                   -- JSON array: "switch" | "light"
    fw_version  TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    state       TEXT DEFAULT 'offline'  -- online | offline | joining
);

CREATE TABLE bus_log (
    timestamp   TEXT NOT NULL,
    addr        INTEGER,
    event       TEXT NOT NULL,          -- join, timeout, state_change, error
    data        TEXT                    -- JSON payload
);
```

- Persists to `/data/ohb.db` (HA add-on data directory, survives updates)
- Provides device lookups by address and hw_id
- Tracks state history in `bus_log` for diagnostics
- Supports backup/export as a single file

### 4.4 Commissioning Flow

When a new node joins the bus:

1. **Controller firmware** receives `JOIN_REQUEST` with hw_id on the RS-485 bus
2. Controller assigns an address, sends `JOIN_ACCEPT`, and publishes an MQTT event:
   `ohb/{controller_id}/event → {"type": "join", "addr": 3, "hw_id": "AA:BB:CC:DD:EE:FF", "dev_type": 17}`
3. **Add-on MQTT client** receives the event → creates a new device record in the registry
4. **Dashboard** shows a notification: _"New device discovered at address 0x03"_
5. User names the device, assigns a room, labels each I/O channel, picks entity types
6. **Add-on** publishes HA auto-discovery messages → entities appear in HA immediately
7. The controller was already handling I/O state, so the new HA entities have live data immediately

No YAML editing. No firmware compilation. No flashing. **Plug in a node and name it.**

### 4.5 Dashboard (Rewrite)

The frontend stays as a lightweight SPA but gets restructured around what actually matters:

#### Views

| View             | Purpose                                                                                                                                    |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **Bus Overview** | Live view of all nodes — address, name, room, state (online/offline), last seen. Color-coded status. Tap a node to configure.              |
| **Node Detail**  | Per-node page: I/O channel names, device classes, entity types, current state (DI/DO values live-updating). Rename, reassign room, delete. |
| **Controller**   | Connection status (USB port, baud rate, connected/disconnected). Bus stats: node count, poll rate, error count. Manual reconnect button.   |
| **Diagnostics**  | Bus health: frames/sec, CRC errors, timeouts, per-node signal quality. Bus log table with filtering.                                       |
| **Settings**     | MQTT broker config, serial port override, log level. Backup/restore database.                                                              |

#### Removed Views

- YAML editor (no firmware compilation)
- Serial port list (auto-detected)
- Build/flash/upload (developer tooling, not runtime)
- Serial monitor (niche debugging, use a terminal)

#### Live Updates

Switch from SSE polling to **WebSocket** for real-time I/O state updates. The add-on subscribes to MQTT state topics and forwards changes to connected WebSocket clients. When a node's digital input changes, the dashboard reflects it within ~200ms.

```
WS /ws/bus
→ {"type": "state", "controller": "ctrl_01", "addr": 1, "di": [1, 0, 0, 0], "do": [1, 1, 0, 0]}
→ {"type": "joined", "controller": "ctrl_01", "addr": 3, "hw_id": "AA:BB:CC:DD:EE:FF"}
→ {"type": "offline", "controller": "ctrl_01", "addr": 2}
→ {"type": "controller_status", "controller": "ctrl_01", "nodes": 3, "bus_voltage": 47.8}
```

---

## 5. Simplified `config.yaml`

```yaml
name: OpenHomeBus
description: Bridge between the OHB RS-485 bus and Home Assistant
version: "0.3.0"
slug: openhomebus
url: https://github.com/rathlinus/OpenHomeBus

arch:
  - amd64
  - aarch64
  - armv7

hassio_api: true
auth_api: true

ingress: true
ingress_port: 0
panel_icon: mdi:transit-connection-variant
panel_title: OpenHomeBus

map:
  - config:rw

discovery:
  - mqtt

options:
  log_level: "INFO"

schema:
  log_level: list(DEBUG|INFO|WARNING|ERROR)

init: false
startup: services
boot: auto
```

**That's it.** MQTT broker is auto-discovered via HA's MQTT integration. Serial port is auto-detected. No passwords, no manual broker config. Everything configurable through the dashboard UI.

---

## 6. Slimmed-Down Dockerfile

```dockerfile
ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-aiohttp \
    py3-paho-mqtt

COPY ohb_dashboard/ /opt/ohb/dashboard/
COPY dashboard/ /opt/ohb/frontend/

WORKDIR /opt/ohb
CMD ["python3", "-m", "ohb_dashboard"]
```

**Removed**: PlatformIO (~800 MB), build-essential, git, platformio core, gcc toolchains.  
**Image size**: ~50 MB (down from ~1.2 GB).

---

## 7. Module Structure (After Rewrite)

```
ohb_dashboard/
├── __init__.py
├── __main__.py          # entry point, load HA options, start app
├── app.py               # aiohttp app, routes, WebSocket handler
├── const.py             # MQTT topics, device types, defaults
├── mqtt_client.py       # MQTT subscribe/publish, event routing
├── ha_discovery.py      # HA MQTT discovery payload generation
├── device_registry.py   # SQLite device storage, CRUD, bus log
└── diagnostics.py       # bus health metrics from MQTT telemetry

dashboard/
├── index.html
├── css/
│   └── style.css
└── js/
    └── app.js
```

**7 Python modules** (down from 10). No serial code, no HDLC codec, no PlatformIO. Every module has a clear, single responsibility.

---

## 8. Data Flow Examples

### User flips a switch in HA → relay toggles on node

```
1. HA publishes:     ohb/ctrl_01/01/do/0/set → "ON"
2. Controller FW:    receives MQTT message (subscribed to ohb/ctrl_01/+/do/+/set)
3. Controller FW:    parses addr=0x01, channel=0, state=ON
4. Controller FW:    encodes HDLC OUTPUT_CMD frame: [0x7E][dst=0x01][src=0xFF][type=OUTPUT_CMD][mask=0x01][state=0x01][CRC16][0x7E]
5. Controller FW:    transmits frame on RS-485 bus
6. Node FW:          receives OUTPUT_CMD, sets DO0=HIGH, sends ACK
7. Controller FW:    next TOKEN poll → node responds with updated I/O state
8. Controller FW:    publishes ohb/ctrl_01/01/state → {"di": [false, false], "do": [true, false]}
9. HA updates:       switch entity shows ON
```

### Node digital input changes → HA entity updates

```
1. Controller FW:    sends TOKEN to node 0x01 (regular 100ms poll)
2. Node FW:          reads GPIOs, responds with TOKEN_RESPONSE: di=0b10, do=0b01
3. Controller FW:    detects DI change (was 0b00, now 0b10)
4. Controller FW:    publishes ohb/ctrl_01/01/state → {"di": [false, true], "do": [true, false]}
5. HA updates:       binary_sensor entity shows ON
```

### New node joins the bus → appears in HA

```
1. Node FW:          powers up, sends JOIN_REQUEST with hw_id on RS-485 bus
2. Controller FW:    receives JOIN_REQUEST, assigns addr=0x03, sends JOIN_ACCEPT
3. Controller FW:    publishes ohb/ctrl_01/event → {"type": "join", "addr": 3, "hw_id": "AA:BB:CC:DD:EE:FF", "dev_type": 17}
4. OHB Add-on:       receives event via MQTT → creates device in SQLite registry
5. Dashboard:        shows "New device discovered" notification
6. User:             names device, assigns room, labels I/O channels
7. OHB Add-on:       publishes HA auto-discovery messages for each entity
8. HA:               entities appear (binary_sensors for DI, switches for DO)
9. Controller FW:    already polling the node → entities have live data immediately
```

Note: steps 1–4 are fully automatic. Steps 5–7 only happen once per device. After commissioning, the controller handles all real-time I/O without add-on involvement.

---

## 9. What Needs to Change in the Controller Firmware

The controller needs to become a **network-capable bus gateway**: Ethernet on one side, RS-485 on the other, with MQTT as the glue.

### Required Firmware Work

1. **W5500 Ethernet driver**: SPI driver for the W5500 (GPIOs 10–13, INT on GPIO9, RST on GPIO8). ESP-IDF has an [official W5500 component](https://components.espressif.com/components/espressif/esp_eth_w5500) — wire it up, get DHCP, done.

2. **MQTT client**: Use ESP-MQTT (built into ESP-IDF). Connect to Mosquitto on the LAN. Subscribe to command topics, publish state and events.

3. **State change detection**: Currently the controller logs I/O state on every poll. Instead, track last-known state per node and only publish MQTT when state changes (or periodically as a heartbeat).

4. **JOIN event publishing**: When a node sends a JOIN_REQUEST, the controller already handles address assignment. Add an MQTT publish of the join event so the add-on can register the new device.

5. **Diagnostics publishing**: Periodically publish bus health (utilization, CRC errors, timeouts, bus voltage) to `ohb/{id}/diagnostics`.

6. **Controller birth/will**: Publish an MQTT birth message on connect, set an LWT (Last Will and Testament) so HA knows when the controller goes offline.

### Already Working (No Changes Needed)

- RS-485 at 500 kbit/s with HDLC framing
- JOIN_REQUEST / JOIN_ACCEPT handshake
- Token-passing poll loop (100ms round-robin)
- OUTPUT_CMD relay control
- I/O state reception from nodes

### Not Required on the Controller

- Web server (the add-on serves the UI)
- Device registry / naming (the add-on manages this)
- HA discovery publishing (the add-on handles this — it knows the user-assigned names, rooms, and entity types)
- Full MQTT broker (a client connecting to Mosquitto is sufficient)

The controller is a **smart gateway**: it handles the real-time bus protocol, translates I/O state to MQTT, and accepts MQTT commands. The add-on handles everything user-facing.

---

## 10. Implementation Phases

### Phase 1 — Controller Ethernet + Basic I/O (MVP)

**Goal**: Turn bus I/O state into HA entities. Flip switches.

**Controller firmware:**

- [ ] W5500 SPI driver + DHCP
- [ ] ESP-MQTT client → connect to Mosquitto
- [ ] Publish I/O state changes to MQTT
- [ ] Subscribe to command topics → send OUTPUT_CMD on bus
- [ ] Publish JOIN events when new nodes appear
- [ ] Birth/will messages for availability

**Add-on:**

- [ ] `mqtt_client.py` — Subscribe to controller topics, event routing
- [ ] `device_registry.py` — SQLite CRUD for devices
- [ ] `ha_discovery.py` — Generate and publish HA discovery payloads
- [ ] `app.py` — Minimal REST API + WebSocket for live state
- [ ] Dashboard — Bus overview + node detail views
- [ ] Tests for all new modules

**Deliverable**: Plug in controller + nodes → they appear in HA → control relays from HA.

### Phase 2 — Commissioning UX + Diagnostics

**Goal**: Make setup smooth and provide operational visibility.

- [ ] Commissioning flow UI (name, room, I/O labels, entity types)
- [ ] Auto-detection of controller USB port
- [ ] Bus diagnostics view (error rates, per-node health)
- [ ] Bus event log (SQLite `bus_log` table + UI)
- [ ] Offline node detection + HA notification
- [ ] Backup/restore database

**Deliverable**: Non-technical user can set up OHB from scratch using only the HA UI.

### Phase 3 — Advanced Features

**Goal**: Production-grade reliability and convenience.

- [ ] OTA firmware updates (when firmware supports it)
- [ ] Multi-segment support (multiple controllers)
- [ ] Topology visualization (SVG bus diagram)
- [ ] Node firmware version tracking + update prompts
- [ ] Automation blueprints for common OHB scenarios
- [ ] Encrypted bus communication (when firmware supports AES-128-CCM)

---

## 11. Key Design Decisions

| Decision                                                          | Rationale                                                                                                                                                                                                                                              |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Controller is the data bridge, add-on is the management layer** | The ESP32-S3 has plenty of resources (512 KB SRAM, 8 MB flash, dual-core 240 MHz) to run MQTT + bus master. Real-time I/O belongs on the controller. User-facing config (naming, rooms, HA discovery) belongs in the add-on where it's easy to update. |
| **No firmware compilation in the add-on**                         | PlatformIO adds ~800 MB to the image and is a developer workflow, not a user workflow. Nodes should ship with (or be flashed with) a standard firmware.                                                                                                |
| **SQLite over JSON**                                              | Concurrent access safety, query capability, single-file backup.                                                                                                                                                                                        |
| **WebSocket over SSE**                                            | Bidirectional, lower overhead, better mobile support.                                                                                                                                                                                                  |
| **Auto-discover everything**                                      | MQTT broker from HA discovery API, controllers from MQTT birth messages, nodes from bus JOIN events. Zero-config is the goal.                                                                                                                          |
| **Simple topic structure**                                        | `ohb/{segment}/{addr}/...` — flat, predictable, debuggable with `mosquitto_sub`.                                                                                                                                                                       |
| **No YAML config files for nodes**                                | Configuration is runtime (name, room, I/O labels) not compile-time. Stored in the database, not in files.                                                                                                                                              |

---

## 12. Dependencies (After Rewrite)

```
aiohttp>=3.9
paho-mqtt>=2.0
aiosqlite>=0.19
```

Three dependencies. No pyserial (controller uses Ethernet), no PlatformIO, no build tools, no git.

---

## 13. Summary

The current add-on was built assuming MQTT-native nodes on the bus — which doesn't exist and isn't needed. The rewrite puts the responsibility where it belongs:

- **The controller** handles real-time I/O: bus polling, state detection, MQTT publish/subscribe — all over Ethernet via its W5500 interface.
- **The add-on** handles management: device commissioning, naming, HA discovery, diagnostics UI.

The result is a clean separation of concerns, a dramatically smaller add-on (~50 MB instead of ~1.2 GB), and a system that works end-to-end once the controller firmware gets Ethernet + MQTT support (ESP-IDF provides both as off-the-shelf components).

**Core principle**: _The controller bridges the bus to the network. The add-on bridges the network to the user._
