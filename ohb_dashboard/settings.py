"""OpenHomeBus Dashboard — Settings manager.

Persists addon settings (MQTT broker, known controllers, etc.) to a
JSON file in the data directory so they survive addon restarts.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .const import DATA_DIR

_LOGGER = logging.getLogger(__name__)

_SETTINGS_FILE = DATA_DIR / "settings.json"

# Keys that are safe to expose via the API
_VISIBLE_KEYS = {
    "mqtt_broker",
    "mqtt_port",
    "mqtt_username",
    "mqtt_password",
    "known_controllers",
}


class SettingsManager:
    """Simple JSON-backed settings store."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._load()

    # ── Read ───────────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def get_all(self) -> dict[str, Any]:
        """Return all settings (password is masked)."""
        out = {k: v for k, v in self._data.items() if k in _VISIBLE_KEYS}
        if "mqtt_password" in out and out["mqtt_password"]:
            out["mqtt_password_set"] = True
            out["mqtt_password"] = "********"
        else:
            out["mqtt_password_set"] = False
            out["mqtt_password"] = ""
        return out

    # ── Write ──────────────────────────────────────────────────────────

    def update(self, values: dict[str, Any]) -> None:
        for k, v in values.items():
            if k in _VISIBLE_KEYS:
                # Don't overwrite password with the mask
                if k == "mqtt_password" and v == "********":
                    continue
                self._data[k] = v
        self._save()

    # ── Persistence ────────────────────────────────────────────────────

    def _load(self) -> None:
        if _SETTINGS_FILE.exists():
            try:
                self._data = json.loads(_SETTINGS_FILE.read_text())
                _LOGGER.info("Settings loaded from %s", _SETTINGS_FILE)
            except Exception:
                _LOGGER.warning("Failed to load settings, using defaults")
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _SETTINGS_FILE.write_text(json.dumps(self._data, indent=2))
            _LOGGER.debug("Settings saved to %s", _SETTINGS_FILE)
        except Exception:
            _LOGGER.exception("Failed to save settings")
