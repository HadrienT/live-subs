"""GPU checks: refuse to run the ASR on CPU silently (WP10 §3), VRAM for /health."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class NoGpuError(RuntimeError):
    pass


def cuda_device_count() -> int:
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception as e:  # missing driver, missing libs
        log.warning("CUDA check failed: %s", e)
        return 0


def require_gpu(device: str, *, allow_cpu: bool) -> str:
    """Device to use. Unlike llama-server on 30/09 (AgenticEnv#15), never degrade to
    CPU without saying so: that only happens with LIVESUBS_ALLOW_CPU=1."""
    kind, _, index = device.partition(":")
    if kind != "cuda":
        return device
    count = cuda_device_count()
    if count > int(index or 0):
        return device
    if allow_cpu:
        log.warning("no CUDA device %s visible: running the ASR on CPU (slow)", device)
        return "cpu"
    raise NoGpuError(
        f"ASR device {device} is not visible (CUDA devices: {count}). Check the NVIDIA "
        "driver and the container's GPU reservation, or set LIVESUBS_ALLOW_CPU=1 to run "
        "slowly on CPU on purpose."
    )


def memory(device: str) -> dict[str, Any] | None:
    """Used / total MiB on the device, and what this process holds there."""
    kind, _, index = device.partition(":")
    if kind != "cuda":
        return None
    try:
        import os

        import pynvml

        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(int(index or 0))
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            # In a container NVML reports host PIDs: then this process is not found (null).
            mine = next(
                (
                    int(proc.usedGpuMemory) // 2**20
                    for proc in pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
                    if proc.pid == os.getpid() and proc.usedGpuMemory
                ),
                None,
            )
            return {
                "used_mib": int(info.used) // 2**20,
                "total_mib": int(info.total) // 2**20,
                "process_mib": mine,
            }
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return None
