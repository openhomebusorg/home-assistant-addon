"""Shared test fixtures for OpenHomeBus Dashboard tests."""

import pytest

from ohb_dashboard.device_registry import DeviceEntry
from ohb_dashboard.const import (
    DEVICE_TYPE_NODE_IO,
    DEVICE_TYPE_NODE_IO_2CH,
    DEVICE_TYPE_CONTROLLER,
    NODE_STATE_ONLINE,
    NODE_STATE_OFFLINE,
)


@pytest.fixture
def sample_device_4ch() -> DeviceEntry:
    """A 4-channel I/O node DeviceEntry for testing."""
    return DeviceEntry(
        address=0x05,
        hw_id="AABBCCDDEEFF",
        name="Test Node",
        controller_id="ctrl1",
        device_type=DEVICE_TYPE_NODE_IO,
        di_count=4,
        do_count=4,
        di_names=["Input 1", "Input 2", "Input 3", "Input 4"],
        do_names=["Output 1", "Output 2", "Output 3", "Output 4"],
        di_classes=["", "motion", "", ""],
        do_types=["switch", "light", "switch", "switch"],
        fw_version="0.1.0",
        first_seen="2025-01-01T00:00:00+00:00",
        last_seen="2025-01-01T00:00:00+00:00",
        state=NODE_STATE_ONLINE,
    )


@pytest.fixture
def sample_device_2ch() -> DeviceEntry:
    """A 2-channel I/O node DeviceEntry for testing."""
    return DeviceEntry(
        address=0x0A,
        hw_id="112233445566",
        name="Small Node",
        controller_id="ctrl1",
        device_type=DEVICE_TYPE_NODE_IO_2CH,
        di_count=2,
        do_count=2,
        di_names=["Input 1", "Input 2"],
        do_names=["Output 1", "Output 2"],
        di_classes=["", ""],
        do_types=["switch", "switch"],
        fw_version="0.2.0",
        state=NODE_STATE_OFFLINE,
    )
