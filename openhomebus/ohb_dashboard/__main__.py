"""OpenHomeBus Dashboard — Entry point."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from aiohttp import web

from .app import create_app
from .const import DEFAULT_PORT

_LOGGER = logging.getLogger("ohb_dashboard")


def _load_ha_options() -> dict:
    """Load Home Assistant add-on options from /data/options.json.

    We only need ``log_level`` — MQTT broker details come from
    HA's Mosquitto add-on (auto-discovered via ``localhost:1883``).
    """
    options_path = Path("/data/options.json")
    if options_path.exists():
        try:
            return json.loads(options_path.read_text())
        except Exception:
            _LOGGER.warning("Failed to read HA options.json, using defaults")
    return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenHomeBus Dashboard")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="Web server port"
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    args = parser.parse_args()

    ha_options = _load_ha_options()
    log_level = ha_options.get("log_level", args.log_level)

    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    port = int(os.environ.get("OHB_PORT", args.port))
    host = os.environ.get("OHB_HOST", args.host)

    _LOGGER.info("Starting OpenHomeBus Dashboard on %s:%d", host, port)

    app = create_app(ha_options)
    web.run_app(app, host=host, port=port, print=_LOGGER.info)


if __name__ == "__main__":
    main()
