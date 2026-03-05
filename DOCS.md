# OpenHomeBus — Home Assistant Add-on

**Configure, build, and update OpenHomeBus devices — like ESPHome for RS-485 home automation.**

The OpenHomeBus add-on provides a web dashboard (accessible from the Home Assistant sidebar) that lets you manage your entire OHB bus: create device configurations in YAML, compile firmware, flash over serial or OTA, and monitor bus traffic — all from your browser.

---

## Features

| Feature | Description |
|---------|-------------|
| **YAML Configuration** | ESPHome-style YAML configs that compile to C firmware |
| **Visual Dashboard** | Dark-themed device management UI in the HA sidebar |
| **Compile Firmware** | PlatformIO builds for ESP32-S3 controllers & STM32G0 nodes |
| **Serial Flash** | Upload firmware via USB from the dashboard |
| **OTA Updates** | Push firmware over MQTT (512-byte chunks with CRC verification) |
| **Device Discovery** | Auto-detect nodes joining the bus |
| **Serial Monitor** | Live log streaming from connected devices |
| **MQTT Integration** | Auto-connects to HA's MQTT broker for discovery |
| **Multi-arch** | Runs on amd64, aarch64 (RPi4/5), and armv7 |

---

## Installation

### As a Home Assistant Add-on

1. **Add the repository** to your Home Assistant add-on store:
   ```
   https://github.com/rathlinus/OpenHomeBus
   ```
2. Find **OpenHomeBus** in the add-on store and click **Install**.
3. Configure MQTT settings in the add-on **Configuration** tab.
4. Click **Start** and open the **Web UI** from the sidebar.

### Standalone (Development)

```bash
cd homeassistant-addon
pip install -r requirements.txt
python -m ohb_dashboard --port 6052
```

Open `http://localhost:6052` in your browser.

---

## Configuration

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `mqtt_broker` | string | `""` | MQTT broker host (auto-detected from HA if blank) |
| `mqtt_port` | int | `1883` | MQTT broker port |
| `mqtt_username` | string | `""` | MQTT username |
| `mqtt_password` | string | `""` | MQTT password |
| `serial_port` | device | `/dev/ttyUSB0` | Default serial port for flashing |
| `ota_password` | string | `""` | Default OTA password for new devices |
| `log_level` | list | `INFO` | Logging verbosity |

---

## How It Works

### 1. Create a Device

Click **New Device** and choose a type:

- **Controller (ESP32-S3)** — Bus master with MQTT bridge, Wi-Fi/Ethernet
- **I/O Node (STM32G0)** — Digital inputs + relay outputs
- **Sensor Node (STM32G0)** — Temperature, humidity, etc.

### 2. Edit Configuration (YAML)

Each device is described by a YAML file, similar to ESPHome:

```yaml
# Example: I/O Node for a light switch
ohb:
  name: "hallway-switch"
  device_type: node_io
  board: nucleo_g031k8

rs485:
  uart: USART2
  baud_rate: 500000
  tx_pin: PA2
  rx_pin: PA3
  de_pin: PA1

digital_inputs:
  - pin: PB0
    name: "Wall Switch"
    entity_type: binary_sensor
    device_class: door
    inverted: true

digital_outputs:
  - pin: PA8
    name: "Ceiling Light"
    entity_type: switch
    device_class: light

status_led:
  pin: PC6

ota:
  password: "my-secret"

logger:
  level: INFO
```

### 3. Build Firmware

Click **Build** — the add-on compiles your YAML into C code, generates `ohb_config.h`, and runs PlatformIO to produce a firmware binary.

### 4. Flash or OTA Update

- **Flash (USB)**: Connect the device via USB, select the serial port, click **Flash**
- **OTA**: If the device is already on the bus, click **OTA** to push the update over MQTT

### 5. Automatic HA Integration

Once flashed, the controller connects to your MQTT broker and publishes Home Assistant auto-discovery messages. Your devices appear in HA automatically — no custom integration needed.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Home Assistant                     │
│  ┌──────────────┐  ┌────────────┐  ┌─────────────┐ │
│  │ OHB Add-on   │  │ MQTT Broker│  │ HA Frontend  │ │
│  │ (Dashboard)  │──│ (Mosquitto)│──│ (Devices UI) │ │
│  └──────┬───────┘  └─────┬──────┘  └─────────────┘ │
│         │                │                           │
│         │ Build/Flash    │ MQTT auto-discovery       │
└─────────┼────────────────┼───────────────────────────┘
          │                │
   ┌──────┴───────┐  ┌─────┴──────┐
   │  USB/Serial  │  │  RS-485    │
   │  (flashing)  │  │  Bus       │
   └──────────────┘  │            │
                     ├── Controller (ESP32-S3)
                     │     ├── MQTT broker bridge
                     │     ├── Bus arbitration
                     │     └── OTA relay
                     │
                     ├── I/O Node (STM32G0)
                     │     ├── 4× Digital inputs
                     │     └── 4× Relay outputs
                     │
                     └── Sensor Node (STM32G0)
                           ├── Temperature
                           └── Humidity
```

---

## YAML Configuration Reference

### `ohb` (required)
| Key | Type | Description |
|-----|------|-------------|
| `name` | string | Device name (used as HA entity prefix) |
| `device_type` | string | `controller`, `node_io`, `node_sensor`, `node_dimmer` |
| `board` | string | PlatformIO board identifier |

### `rs485` (required)
| Key | Type | Description |
|-----|------|-------------|
| `uart` | string | UART peripheral (e.g. `USART2`, `UART1`) |
| `baud_rate` | int | Bus speed (default: `500000`) |
| `tx_pin` | string | TX pin |
| `rx_pin` | string | RX pin |
| `de_pin` | string | RS-485 driver enable pin |

### `network` (controller only)
| Key | Type | Description |
|-----|------|-------------|
| `segment` | int | Bus segment ID (1–254) |
| `mqtt.broker` | string | MQTT broker address |
| `mqtt.port` | int | MQTT port (default: `1883`) |
| `mqtt.username` | string | MQTT username |
| `mqtt.password` | string | MQTT password |

### `digital_inputs` (node_io)
| Key | Type | Description |
|-----|------|-------------|
| `pin` | string | GPIO pin |
| `name` | string | Human-readable name |
| `entity_type` | string | HA entity type (`binary_sensor`) |
| `device_class` | string | HA device class |
| `inverted` | bool | Invert logic level |

### `digital_outputs` (node_io)
| Key | Type | Description |
|-----|------|-------------|
| `pin` | string | GPIO pin |
| `name` | string | Human-readable name |
| `entity_type` | string | HA entity type (`switch`, `light`) |
| `device_class` | string | HA device class |

### `sensors` (node_sensor)
| Key | Type | Description |
|-----|------|-------------|
| `type` | string | Sensor type (`temperature`, `humidity`, etc.) |
| `name` | string | Human-readable name |
| `pin` | string | ADC pin |
| `update_interval` | string | Reading interval (`30s`, `5m`, `1h`) |
| `unit` | string | Unit of measurement |
| `accuracy_decimals` | int | Decimal places |

### `ota`
| Key | Type | Description |
|-----|------|-------------|
| `password` | string | OTA update password |

### `logger`
| Key | Type | Description |
|-----|------|-------------|
| `level` | string | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## OTA Update Protocol

The add-on implements the OHB OTA protocol over MQTT:

1. **Announce** → `ohb/ota/announce` with firmware metadata
2. **ACK** ← Device responds on `ohb/ota/status/{device_id}`
3. **Stream** → 512-byte chunks to `ohb/ota/data/{device_id}/{chunk_idx}` (each with CRC-16)
4. **Verify** → CRC-32 full-image check + Ed25519 signature validation
5. **Swap** → A/B partition swap with 30-second health check
6. **Rollback** → Automatic rollback if health check fails

---

## Development

```bash
# Install dev dependencies
pip install -r requirements.txt

# Run with live reload
python -m ohb_dashboard --port 6052 --log-level DEBUG

# Run tests
python -m pytest tests/
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/info` | Dashboard version & MQTT status |
| GET | `/api/devices` | List all devices |
| POST | `/api/devices` | Create a new device |
| GET | `/api/devices/{id}` | Get device details |
| PUT | `/api/devices/{id}` | Update device |
| DELETE | `/api/devices/{id}` | Delete device |
| GET | `/api/devices/{id}/config` | Get YAML config |
| PUT | `/api/devices/{id}/config` | Save YAML config |
| POST | `/api/devices/{id}/validate` | Validate config |
| POST | `/api/devices/{id}/build` | Build firmware (SSE stream) |
| POST | `/api/devices/{id}/upload` | Flash via serial (SSE stream) |
| POST | `/api/devices/{id}/ota` | Start OTA update |
| GET | `/api/devices/{id}/ota-status` | OTA progress |
| GET | `/api/devices/{id}/firmware` | Download firmware binary |
| GET | `/api/serial-ports` | List serial ports |
| GET | `/api/devices/{id}/logs` | Serial log stream (SSE) |
| GET | `/api/templates` | List device templates |
| POST | `/api/templates/generate` | Generate config from template |
| GET | `/api/mqtt/status` | MQTT connection status |
