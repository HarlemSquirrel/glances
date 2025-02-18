# -*- coding: utf-8 -*-
#
# This file is part of Glances.
#
# SPDX-FileCopyrightText: 2024 Nicolas Hennion <nicolas@nicolargo.com>
#
# SPDX-License-Identifier: LGPL-3.0-only
#

"""NVidia Extension unit for Glances' GPU plugin."""

from glances.logger import logger
from glances.globals import nativestr

try:
    import pynvml
except Exception as e:
    nvidia_gpu_enable = False
    # Display debug message if import KeyError
    logger.warning("Missing Python Lib ({}), Nvidia GPU plugin is disabled".format(e))
else:
    nvidia_gpu_enable = True


class NvidiaGPU:
    """GPU card class."""

    def __init__(self):
        """Init Nvidia GPU card class."""
        if not nvidia_gpu_enable:
            self.device_handles = []
        else:
            try:
                pynvml.nvmlInit()
                self.device_handles = get_device_list()
            except Exception:
                logger.debug("pynvml could not be initialized.")
                self.device_handles = []

    def exit(self):
        """Close NVidia GPU class."""
        if self.device_handles != []:
            try:
                pynvml.nvmlShutdown()
            except Exception as e:
                logger.debug("pynvml failed to shutdown correctly ({})".format(e))

    def get_device_stats(self):
        """Get Nvidia GPU stats."""
        stats = []

        for index, device_handle in enumerate(self.device_handles):
            device_stats = dict()
            # Dictionary key is the GPU_ID
            device_stats['key'] = 'gpu_id'
            # GPU id (for multiple GPU, start at 0)
            device_stats['gpu_id'] = f'nvidia{index}'
            # GPU name
            device_stats['name'] = get_device_name(device_handle)
            # Memory consumption in % (not available on all GPU)
            device_stats['mem'] = get_mem(device_handle)
            # Processor consumption in %
            device_stats['proc'] = get_proc(device_handle)
            # Processor temperature in °C
            device_stats['temperature'] = get_temperature(device_handle)
            # Fan speed in %
            device_stats['fan_speed'] = get_fan_speed(device_handle)
            stats.append(device_stats)

        return stats


def get_device_list():
    """Get a list of NVML device handles, one per device.

    Can throw NVMLError.
    """
    return [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(pynvml.nvmlDeviceGetCount())]


def get_device_name(device_handle):
    """Get GPU device name."""
    try:
        return nativestr(pynvml.nvmlDeviceGetName(device_handle))
    except pynvml.NVMLError:
        return "NVIDIA"


def get_mem(device_handle):
    """Get GPU device memory consumption in percent."""
    try:
        memory_info = pynvml.nvmlDeviceGetMemoryInfo(device_handle)
        return memory_info.used * 100.0 / memory_info.total
    except pynvml.NVMLError:
        return None


def get_proc(device_handle):
    """Get GPU device CPU consumption in percent."""
    try:
        return pynvml.nvmlDeviceGetUtilizationRates(device_handle).gpu
    except pynvml.NVMLError:
        return None


def get_temperature(device_handle):
    """Get GPU device CPU temperature in Celsius."""
    try:
        return pynvml.nvmlDeviceGetTemperature(device_handle, pynvml.NVML_TEMPERATURE_GPU)
    except pynvml.NVMLError:
        return None


def get_fan_speed(device_handle):
    """Get GPU device fan speed in percent."""
    try:
        return pynvml.nvmlDeviceGetFanSpeed(device_handle)
    except pynvml.NVMLError:
        return None
