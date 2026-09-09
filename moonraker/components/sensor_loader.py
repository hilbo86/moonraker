# Helper class to integrate misc. sensor classes
#
# Copyright (C) 2025 Timo Hilbig <timo@t-hilbig.de>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations

import importlib
import logging
import re
from .sensor import BaseSensor

# Annotation imports
from typing import (
    Dict,
    Optional,
    TYPE_CHECKING,
    Type,
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper

class SensorLoader:
    def __init__(self, config: ConfigHelper) -> None:
        self.sensor_classes: Dict[str, Type[BaseSensor]] = {}
        self.failed_sensors: set[str] = set()

    def import_sensor(self, sensor_type: str) -> Optional[Type[BaseSensor]]:
        sensor_type = sensor_type.lower()
        if sensor_type in self.sensor_classes:
            return self.sensor_classes[sensor_type]
        if sensor_type in self.failed_sensors:
            return None
        if re.fullmatch(r"[a-z][a-z0-9_]*", sensor_type) is None:
            logging.error("Invalid sensor type module name: %s", sensor_type)
            self.failed_sensors.add(sensor_type)
            return None
        full_name = f"moonraker.components.{sensor_type}"
        try:
            module = importlib.import_module(full_name)
            load_func = getattr(module, "load_sensor_class")
            sensor_class = load_func()
            if not isinstance(sensor_class, type) or not issubclass(
                sensor_class, BaseSensor
            ):
                raise TypeError(
                    f"{full_name}.load_sensor_class() did not return a "
                    "BaseSensor subclass"
                )
        except Exception as e:
            logging.exception("Unable to load sensor type '%s': %s", sensor_type, e)
            self.failed_sensors.add(sensor_type)
            return None
        self.sensor_classes[sensor_type] = sensor_class
        logging.info("Sensor type '%s' loaded", sensor_type)
        return sensor_class

def load_component(config: ConfigHelper) -> SensorLoader:
    return SensorLoader(config)
