"""Device roster for a simulated home."""

from __future__ import annotations

from dataclasses import dataclass

TRANSIT = "transit"
INTERIOR = "interior"


@dataclass(frozen=True)
class Device:
    device_id: str
    name: str
    zone_class: str
    covers: tuple[str, ...]
    motion_per_hour: float
    cooldown_s: int
    supports_ding: bool = False


DEFAULT_DEVICES: tuple[Device, ...] = (
    Device("front_door", "Front Door", TRANSIT, ("entry",), 40.0, 30, True),
    Device("back_door", "Back Door", TRANSIT, ("back_entry",), 40.0, 30),
    Device("hallway", "Hallway", INTERIOR, ("hallway",), 45.0, 30),
    Device("kitchen", "Kitchen", INTERIOR, ("kitchen",), 26.0, 45),
    Device("living_room", "Living Room", INTERIOR, ("living_room",), 9.0, 60),
    Device("landing", "Landing", INTERIOR, ("bathroom", "bedroom"), 20.0, 45),
)


def by_id(devices: tuple[Device, ...] = DEFAULT_DEVICES) -> dict[str, Device]:
    return {device.device_id: device for device in devices}


def covering(zone: str, devices: tuple[Device, ...] = DEFAULT_DEVICES) -> list[Device]:
    return [device for device in devices if zone in device.covers]


def manifest(devices: tuple[Device, ...] = DEFAULT_DEVICES) -> list[dict]:
    return [
        {
            "device_id": device.device_id,
            "name": device.name,
            "zone_class": device.zone_class,
        }
        for device in devices
    ]
