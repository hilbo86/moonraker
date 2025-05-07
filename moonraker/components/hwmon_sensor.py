# Support for HWMON Sensors on Linux systems
#
# Copyright (C) 2025 Timo Hilbig <timo@t-hilbig.de>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations

from .sensor import BaseSensor

# Annotation imports
from typing import (
    #Any,
    #DefaultDict,
    #Deque,
    #Dict,
    #List,
    #Optional,
    #Type,
    TYPE_CHECKING,
    #Union,
    #Callable
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    #from ..common import WebRequest
    #from .history import History

SENSOR_UPDATE_TIME = 1.0

class HWMONSensor(BaseSensor):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config=config)

    def initialize(self):
        pass

def load_component(config: ConfigHelper) -> HWMONSensor:
    return HWMONSensor(config)

def load_sensor_class() -> HWMONSensor:
    return HWMONSensor