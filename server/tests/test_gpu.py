import pytest

from livesubs import gpu


def test_cpu_device_passes_through() -> None:
    assert gpu.require_gpu("cpu", allow_cpu=False) == "cpu"


def test_refuses_to_start_without_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu, "cuda_device_count", lambda: 0)
    with pytest.raises(gpu.NoGpuError, match="LIVESUBS_ALLOW_CPU"):
        gpu.require_gpu("cuda:0", allow_cpu=False)
    assert gpu.require_gpu("cuda:0", allow_cpu=True) == "cpu"


def test_device_index_must_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu, "cuda_device_count", lambda: 1)
    assert gpu.require_gpu("cuda:0", allow_cpu=False) == "cuda:0"
    with pytest.raises(gpu.NoGpuError):
        gpu.require_gpu("cuda:1", allow_cpu=False)
