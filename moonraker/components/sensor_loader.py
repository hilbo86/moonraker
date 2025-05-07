# Helper class to integrate misc. sensor classes
#
# Copyright (C) 2025 Timo Hilbig <timo@t-hilbig.de>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations

import importlib
import logging
from .sensor import BaseSensor
from ..utils import Sentinel

# Annotation imports
from typing import (
    Any,
    DefaultDict,
    Deque,
    Dict,
    List,
    Optional,
    Type,
    TYPE_CHECKING,
    Union,
    Callable
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper

class SensorLoader:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.components: Dict = {str, BaseSensor}

    def import_sensor(self, sensor_type: str, default=None) -> BaseSensor | None:
        if sensor_type in self.components:
            return self.components[sensor_type]
        elif sensor_type in self.server.failed_components:
            return None
        full_name = f"moonraker.components.{sensor_type}"
        try:
            module = importlib.import_module(full_name)
            load_func = getattr(module, "load_sensor_class")
            component = load_func()
        except Exception as e:
            msg = f"Unable to load component: ({sensor_type})"
            logging.exception(msg)
            if sensor_type not in self.server.failed_components:
                self.server.failed_components.append(sensor_type)
            return None
        self.components[sensor_type] = component
        logging.info(f"Component ({sensor_type}) loaded")
        return component
    
def load_component(config: ConfigHelper) -> SensorLoader:
    return SensorLoader(config)
