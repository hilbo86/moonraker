# Support for HWMON sensors on Linux systems
#
# Copyright (C) 2025 Timo Hilbig <timo@t-hilbig.de>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations

import fnmatch
import logging
import math
import pathlib
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, TYPE_CHECKING, Tuple, Union

from .sensor import BaseSensor

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper

HWMON_ROOT_PATH = "/sys/class/hwmon"
DEFAULT_INPUT_PATTERNS = ["temp*_input", "fan*_input"]
INPUT_NAME_RE = re.compile(r"^(temp|fan)(\d+)_input$")
PARAMETER_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class HwmonChannel:
    name: str
    path: pathlib.Path
    units: str
    scale: float
    integral: bool


class HWMONSensor(BaseSensor):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config=config)
        self.event_loop = self.server.get_event_loop()
        self.hwmon_path = pathlib.Path(
            config.get("path", HWMON_ROOT_PATH)
        ).expanduser()
        self.chip: Optional[str] = config.get("chip", None)
        self.channel_config: Dict[str, str] = config.getdict("channels", {})
        self.input_patterns: List[str] = config.getlist(
            "include", DEFAULT_INPUT_PATTERNS
        )
        self.exclude_patterns: List[str] = config.getlist("exclude", [])
        self.poll_interval: float = config.getfloat(
            "poll_interval", 1.0, minval=1.0
        )
        self.next_poll_time = 0.0
        self.channels: List[HwmonChannel] = []
        for name in self.channel_config:
            if PARAMETER_NAME_RE.fullmatch(name) is None:
                raise config.error(
                    f"[{config.get_name()}]: Invalid channel name '{name}'. "
                    "Names must contain only letters, numbers, and underscores."
                )

    @staticmethod
    def _read_text(path: pathlib.Path) -> str:
        return path.read_text(encoding="utf-8").strip()

    @staticmethod
    def _slugify(value: str) -> str:
        value = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
        return value or "sensor"

    @staticmethod
    def _natural_key(path: pathlib.Path) -> List[Union[str, int]]:
        return [
            int(part) if part.isdigit() else part
            for part in re.split(r"(\d+)", path.name)
        ]

    def _get_devices(self) -> List[Tuple[pathlib.Path, str]]:
        root = self.hwmon_path
        if not root.is_dir():
            raise FileNotFoundError(f"HWMON path does not exist: {root}")
        if (root / "name").is_file():
            paths = [root]
        else:
            paths = sorted(
                (path for path in root.glob("hwmon*") if path.is_dir()),
                key=self._natural_key,
            )
        devices: List[Tuple[pathlib.Path, str]] = []
        for path in paths:
            name_file = path / "name"
            try:
                chip_name = self._read_text(name_file)
            except OSError:
                chip_name = path.name
            if self.chip is not None and self.chip.casefold() not in {
                chip_name.casefold(),
                path.name.casefold(),
            }:
                continue
            devices.append((path, chip_name))
        if not devices:
            selector = f" matching chip '{self.chip}'" if self.chip else ""
            raise FileNotFoundError(f"No HWMON devices found in {root}{selector}")
        return devices

    def _get_available_inputs(
        self, devices: List[Tuple[pathlib.Path, str]]
    ) -> List[Tuple[pathlib.Path, str]]:
        inputs: Dict[pathlib.Path, str] = {}
        for device_path, chip_name in devices:
            for pattern in self.input_patterns:
                for input_path in device_path.glob(pattern):
                    if (
                        input_path.is_file()
                        and INPUT_NAME_RE.fullmatch(input_path.name)
                        and not self._is_excluded(input_path, chip_name)
                    ):
                        inputs[input_path] = chip_name
        return sorted(inputs.items(), key=lambda item: str(item[0]))

    @staticmethod
    def _channel_selectors(
        input_path: pathlib.Path, chip_name: str
    ) -> Tuple[str, ...]:
        device_name = input_path.parent.name
        return (
            input_path.name,
            f"{device_name}/{input_path.name}",
            f"{chip_name}/{input_path.name}",
            input_path.as_posix(),
        )

    def _is_excluded(self, input_path: pathlib.Path, chip_name: str) -> bool:
        selectors = tuple(
            selector.casefold()
            for selector in self._channel_selectors(input_path, chip_name)
        )
        for pattern in self.exclude_patterns:
            normalized = pattern.replace("\\", "/").casefold()
            if any(
                fnmatch.fnmatchcase(selector, normalized)
                for selector in selectors
            ):
                return True
        return False

    @staticmethod
    def _channel_properties(input_path: pathlib.Path) -> Tuple[str, float, bool]:
        match = INPUT_NAME_RE.fullmatch(input_path.name)
        if match is None:
            raise ValueError(f"Unsupported HWMON input: {input_path.name}")
        if match.group(1) == "temp":
            return "°C", 0.001, False
        return "rpm", 1.0, True

    def _resolve_configured_channel(
        self,
        selector: str,
        available: List[Tuple[pathlib.Path, str]],
    ) -> pathlib.Path:
        selector = selector.replace("\\", "/")
        matches: List[pathlib.Path] = []
        for input_path, chip_name in available:
            if selector in self._channel_selectors(input_path, chip_name):
                matches.append(input_path)
        if not matches:
            raise ValueError(f"HWMON channel selector not found: {selector}")
        if len(matches) > 1:
            raise ValueError(
                f"HWMON channel selector is ambiguous: {selector}. "
                "Use hwmonN/input or an absolute path."
            )
        return matches[0]

    def _make_channel(self, name: str, input_path: pathlib.Path) -> HwmonChannel:
        units, scale, integral = self._channel_properties(input_path)
        return HwmonChannel(name, input_path, units, scale, integral)

    def _discover_channels(self) -> List[HwmonChannel]:
        devices = self._get_devices()
        available = self._get_available_inputs(devices)
        if not available:
            raise FileNotFoundError(
                f"No temperature or fan inputs found in {self.hwmon_path}"
            )
        if self.channel_config:
            return [
                self._make_channel(
                    name,
                    self._resolve_configured_channel(selector, available),
                )
                for name, selector in self.channel_config.items()
            ]

        multiple_devices = len({path.parent for path, _ in available}) > 1
        channels: List[HwmonChannel] = []
        used_names: Dict[str, int] = {}
        for input_path, chip_name in available:
            match = INPUT_NAME_RE.fullmatch(input_path.name)
            assert match is not None
            input_type, number = match.groups()
            label_path = input_path.with_name(f"{input_type}{number}_label")
            try:
                label = self._read_text(label_path)
            except OSError:
                label = f"{input_type}{number}"
            suffix = "temperature" if input_type == "temp" else "rpm"
            name_parts = [label, suffix]
            if multiple_devices:
                name_parts.insert(0, chip_name)
            base_name = self._slugify("_".join(name_parts))
            count = used_names.get(base_name, 0) + 1
            used_names[base_name] = count
            name = base_name if count == 1 else f"{base_name}_{count}"
            channels.append(self._make_channel(name, input_path))
        return channels

    def _add_parameter_info(self) -> None:
        configured = {item["name"]: item for item in self.param_info}
        for channel in self.channels:
            info = configured.get(channel.name)
            if info is None:
                self.param_info.append(
                    {"name": channel.name, "units": channel.units}
                )
            else:
                info.setdefault("units", channel.units)

    def _read_measurements(
        self,
    ) -> Tuple[Dict[str, Union[int, float]], List[str]]:
        measurements: Dict[str, Union[int, float]] = {}
        errors: List[str] = []
        for channel in self.channels:
            fault_path = channel.path.with_name(
                channel.path.name.replace("_input", "_fault")
            )
            try:
                if fault_path.is_file() and int(self._read_text(fault_path)):
                    raise ValueError("hardware reports a channel fault")
                raw_value = float(self._read_text(channel.path))
                if not math.isfinite(raw_value):
                    raise ValueError("value is not finite")
                value = raw_value * channel.scale
                measurements[channel.name] = (
                    int(round(value)) if channel.integral else round(value, 3)
                )
            except (OSError, ValueError) as e:
                errors.append(f"{channel.path.name}: {e}")
        return measurements, errors

    async def poll(self, eventtime: float) -> None:
        if eventtime < self.next_poll_time:
            return
        self.next_poll_time = eventtime + self.poll_interval
        measurements, errors = await self.event_loop.run_in_thread(
            self._read_measurements
        )
        error = "; ".join(errors) if errors else None
        if error != self.error_state:
            if error is None:
                if self.error_state is not None:
                    logging.info("HWMON sensor '%s' recovered", self.name)
            else:
                logging.warning("HWMON sensor '%s': %s", self.name, error)
        self.error_state = error
        self._set_measurements(measurements)

    async def initialize(self) -> bool:
        await super().initialize()
        try:
            self.channels = await self.event_loop.run_in_thread(
                self._discover_channels
            )
            self._add_parameter_info()
            await self.poll(0.0)
        except Exception as e:
            self.error_state = str(e)
            logging.exception("Unable to initialize HWMON sensor '%s'", self.name)
            return False
        return bool(self.last_measurements)


def load_component(config: ConfigHelper) -> HWMONSensor:
    return HWMONSensor(config)


def load_sensor_class() -> type[HWMONSensor]:
    return HWMONSensor
