from __future__ import annotations

import pathlib
import tempfile
import unittest
from typing import Any, Dict

from moonraker.components.hwmon_sensor import HWMONSensor
from moonraker.components.sensor import (
    SENSOR_ERROR_EVENT_NAME,
    SENSOR_EVENT_NAME,
    Sensors,
)
from moonraker.components.sensor_loader import SensorLoader


class FakeHistory:
    def register_auxiliary_field(self, field: Any) -> None:
        pass


class FakeEventLoop:
    async def run_in_thread(self, callback, *args):
        return callback(*args)


class FakeServer:
    def __init__(self) -> None:
        self.event_loop = FakeEventLoop()
        self.history = FakeHistory()
        self.events = []

    def get_event_loop(self) -> FakeEventLoop:
        return self.event_loop

    def lookup_component(self, name: str) -> FakeHistory:
        assert name == "history"
        return self.history

    def send_event(self, name: str, data: Any) -> None:
        self.events.append((name, data))


class FakeConfig:
    error = ValueError

    def __init__(self, path: pathlib.Path, **options: Any) -> None:
        self.server = FakeServer()
        self.options: Dict[str, Any] = {
            "type": "hwmon_sensor",
            "path": str(path),
            **options,
        }

    def get_server(self) -> FakeServer:
        return self.server

    def get_name(self) -> str:
        return "sensor hwmon_test"

    def get_options(self) -> Dict[str, Any]:
        return self.options

    def get(self, option: str, default: Any = None) -> Any:
        return self.options.get(option, default)

    def getint(self, option: str, default: Any = None, **kwargs: Any) -> Any:
        return int(self.options.get(option, default))

    def getfloat(self, option: str, default: Any = None, **kwargs: Any) -> Any:
        return float(self.options.get(option, default))

    def getdict(self, option: str, default: Any = None) -> Any:
        return self.options.get(option, default)

    def getlist(self, option: str, default: Any = None) -> Any:
        return self.options.get(option, default)


def write_hwmon_file(device: pathlib.Path, name: str, value: str) -> None:
    device.joinpath(name).write_text(value, encoding="utf-8")


class HwmonSensorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.hwmon_root = pathlib.Path(self.temp_dir.name)

    async def test_auto_discovers_and_scales_channels(self) -> None:
        device = self.hwmon_root / "hwmon0"
        device.mkdir()
        write_hwmon_file(device, "name", "coretemp\n")
        write_hwmon_file(device, "temp1_label", "Package id 0\n")
        write_hwmon_file(device, "temp1_input", "42500\n")
        write_hwmon_file(device, "fan1_input", "1450\n")

        config = FakeConfig(self.hwmon_root)
        sensor = HWMONSensor(config)  # type: ignore[arg-type]

        self.assertTrue(await sensor.initialize())
        self.assertEqual(
            sensor.last_measurements,
            {
                "fan1_rpm": 1450,
                "package_id_0_temperature": 42.5,
            },
        )
        self.assertEqual(
            sensor.param_info,
            [
                {"name": "fan1_rpm", "units": "rpm"},
                {"name": "package_id_0_temperature", "units": "°C"},
            ],
        )

        write_hwmon_file(device, "temp1_input", "43125\n")
        await sensor.poll(1.0)
        self.assertEqual(
            sensor.last_measurements["package_id_0_temperature"], 43.125
        )

    async def test_explicit_channels_and_faults(self) -> None:
        device = self.hwmon_root / "hwmon3"
        device.mkdir()
        write_hwmon_file(device, "name", "nct6798\n")
        write_hwmon_file(device, "temp2_input", "38000\n")
        write_hwmon_file(device, "fan1_input", "900\n")
        config = FakeConfig(
            self.hwmon_root,
            chip="nct6798",
            channels={
                "cpu_temperature": "temp2_input",
                "case_fan_rpm": "fan1_input",
            },
        )
        sensor = HWMONSensor(config)  # type: ignore[arg-type]

        self.assertTrue(await sensor.initialize())
        self.assertEqual(
            sensor.last_measurements,
            {"cpu_temperature": 38.0, "case_fan_rpm": 900},
        )

        write_hwmon_file(device, "fan1_fault", "1\n")
        manager = Sensors.__new__(Sensors)
        manager.server = config.server
        manager.sensors = {"hwmon_test": sensor}
        self.assertEqual(await manager._update_sensor_values(1.0), 2.0)
        self.assertEqual(sensor.last_measurements, {"cpu_temperature": 38.0})
        self.assertIsNotNone(sensor.error_state)
        assert sensor.error_state is not None
        self.assertIn("fan1_input", sensor.error_state)
        self.assertEqual(
            config.server.events,
            [
                (
                    SENSOR_EVENT_NAME,
                    {"hwmon_test": {"cpu_temperature": 38.0}},
                ),
                (
                    SENSOR_ERROR_EVENT_NAME,
                    {
                        "hwmon_test": (
                            "fan1_input: hardware reports a channel fault"
                        )
                    },
                ),
            ],
        )

        write_hwmon_file(device, "fan1_fault", "0\n")
        await manager._update_sensor_values(2.0)
        self.assertEqual(
            config.server.events[-1],
            (SENSOR_ERROR_EVENT_NAME, {"hwmon_test": None}),
        )

    async def test_excludes_filename_and_chip_patterns(self) -> None:
        device = self.hwmon_root / "hwmon4"
        device.mkdir()
        write_hwmon_file(device, "name", "it8603\n")
        write_hwmon_file(device, "temp1_input", "39000\n")
        write_hwmon_file(device, "temp4_input", "127000\n")
        write_hwmon_file(device, "temp5_input", "127000\n")
        write_hwmon_file(device, "fan1_input", "1100\n")
        write_hwmon_file(device, "fan3_input", "0\n")
        write_hwmon_file(device, "fan4_input", "0\n")
        config = FakeConfig(
            self.hwmon_root,
            exclude=["it8603/temp[4-5]_input", "fan[3-4]_input"],
        )
        sensor = HWMONSensor(config)  # type: ignore[arg-type]

        self.assertTrue(await sensor.initialize())
        self.assertEqual(
            sensor.last_measurements,
            {"fan1_rpm": 1100, "temp1_temperature": 39.0},
        )

    async def test_reports_missing_devices(self) -> None:
        config = FakeConfig(self.hwmon_root)
        sensor = HWMONSensor(config)  # type: ignore[arg-type]

        self.assertFalse(await sensor.initialize())
        self.assertEqual(sensor.last_measurements, {})
        self.assertIsNotNone(sensor.error_state)
        assert sensor.error_state is not None
        self.assertIn("No HWMON devices", sensor.error_state)

    def test_runtime_loader_validates_and_caches_sensor_class(self) -> None:
        config = FakeConfig(self.hwmon_root)
        loader = SensorLoader(config)  # type: ignore[arg-type]

        self.assertIs(loader.import_sensor("hwmon_sensor"), HWMONSensor)
        self.assertIs(loader.import_sensor("HWMON_SENSOR"), HWMONSensor)
        self.assertIsNone(loader.import_sensor("../hwmon_sensor"))


if __name__ == "__main__":
    unittest.main()
