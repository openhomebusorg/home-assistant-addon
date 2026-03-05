# Changelog

## 0.4.15

### Fixed

- Removing a controller now properly disconnects it from MQTT (sends clear-config to the ESP32 over HTTP)
- Blocked removed controllers from re-appearing via stale MQTT messages (DiagnosticsManager blocklist)
- Removing a controller now also removes its IP from the known controllers list
- Re-provisioning a previously removed controller correctly unblocks it

## 0.2.0 — Dashboard Redesign

### Changed

- Redesigned dashboard UI to match OpenHomeBus website design language
- Replaced MDI icons with Lucide for a consistent, purpose-driven icon library
- Aligned color tokens with website palette (accent #ff5c00, neutral grays)
- Added website-style section labels (numbered + accent line + uppercase text)
- Device and diagnostics cards now use 1px-gap grid layout with accent hover underlines
- Sidebar active state uses accent indicator bar
- Topbar uses translucent backdrop blur matching the website nav
- Form labels and section headers use uppercase tracking pattern
- Toasts use left accent border for cleaner status signaling
- Diagnostics stat values use large bold typography with separated unit labels
- Dark mode updated to match website deep-black palette
- Tightened spacing and improved visual hierarchy throughout

## 0.1.0 — Initial Release

### Added

- Web dashboard with dark theme, accessible via HA sidebar (ingress)
- ESPHome-style YAML device configuration
- Device templates for Controller (ESP32-S3), I/O Node (STM32G0), Sensor Node
- YAML → C code generation (`ohb_config.h`) with full validation
- PlatformIO firmware compilation from the dashboard
- Serial flash (upload) via USB with port auto-detection
- OTA update over MQTT (512-byte chunked binary with CRC-16/CRC-32)
- Real-time build log streaming (Server-Sent Events)
- Serial monitor (live device log viewer)
- MQTT integration with Home Assistant auto-discovery
- Device management (CRUD, state tracking, bus discovery)
- Multi-architecture Docker support (amd64, aarch64, armv7)
