from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.services import faceshield
from app.services.faceshield import (
    FaceShieldAdapter,
    FaceShieldConfig,
    FaceShieldExecutionError,
    FaceShieldNoFaceDetectedError,
    FaceShieldTimeoutError,
    FaceShieldUnavailableError,
)

INPUT_PNG = b"\x89PNG\r\n\x1a\ninput"
OUTPUT_PNG = b"\x89PNG\r\n\x1a\nprotected"


class FakeProcess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        on_wait: Callable[[], None] | None = None,
        delay: float = 0,
    ) -> None:
        self.pid = 4242
        self.returncode: int | None = None
        self._final_returncode = returncode
        self._on_wait = on_wait
        self._delay = delay
        self._killed = asyncio.Event()

    async def wait(self) -> int:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._on_wait is not None:
            self._on_wait()
            self._on_wait = None
        self.returncode = self._final_returncode
        return self._final_returncode

    def kill(self) -> None:
        self.returncode = -9
        self._final_returncode = -9
        self._killed.set()


class HangingProcess(FakeProcess):
    async def wait(self) -> int:
        await self._killed.wait()
        return -9


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "external-faceshield"
    repo.mkdir()
    return repo


def _config(repo: Path | None, **overrides: Any) -> FaceShieldConfig:
    values: dict[str, Any] = {
        "repo_path": repo,
        "command": ("configured-runner", "execute.sh"),
        "timeout_seconds": 1,
    }
    values.update(overrides)
    return FaceShieldConfig(**values)


def test_protect_png_invokes_official_cli_arguments_and_cleans_temp_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    invocation: dict[str, Any] = {}
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-reach-faceshield")

    async def create_process(*args: str, **kwargs: Any) -> FakeProcess:
        invocation["args"] = args
        invocation["kwargs"] = kwargs
        input_path = Path(args[-1])
        output_path = Path(args[2])
        invocation["request_path"] = input_path.parent
        assert input_path.read_bytes() == INPUT_PNG

        def create_result() -> None:
            result = output_path / "total_iter30" / "step_size1" / "[input]"
            result.mkdir(parents=True)
            (result / "protected.png").write_bytes(OUTPUT_PNG)

        return FakeProcess(on_wait=create_result)

    monkeypatch.setattr(faceshield.asyncio, "create_subprocess_exec", create_process)

    result = asyncio.run(FaceShieldAdapter(_config(repo)).protect_png(INPUT_PNG))

    assert result == OUTPUT_PNG
    assert invocation["args"][:2] == ("configured-runner", "execute.sh")
    assert invocation["args"][3:] == (
        "512",
        "l1",
        "l2",
        "0.2",
        "cosine",
        "30",
        "12",
        "1",
        invocation["args"][-1],
    )
    assert invocation["kwargs"]["cwd"] == repo
    assert invocation["kwargs"]["env"]["PATH"] == faceshield.os.environ["PATH"]
    assert "GEMINI_API_KEY" not in invocation["kwargs"]["env"]
    assert "API_GEMINI_KEY" not in invocation["kwargs"]["env"]
    assert invocation["kwargs"] == {
        "cwd": repo,
        "env": invocation["kwargs"]["env"],
        "stdin": asyncio.subprocess.DEVNULL,
        "stdout": asyncio.subprocess.DEVNULL,
        "stderr": asyncio.subprocess.DEVNULL,
        "start_new_session": True,
    }
    assert not invocation["request_path"].exists()


def test_protect_png_serializes_concurrent_gpu_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    active = 0
    maximum_active = 0

    async def create_process(*args: str, **_: Any) -> FakeProcess:
        output_path = Path(args[2])

        class SerializedProcess(FakeProcess):
            async def wait(self) -> int:
                nonlocal active, maximum_active
                active += 1
                maximum_active = max(maximum_active, active)
                await asyncio.sleep(0.02)
                result = output_path / "nested"
                result.mkdir(parents=True)
                (result / "protected.png").write_bytes(OUTPUT_PNG)
                active -= 1
                self.returncode = 0
                return 0

        return SerializedProcess()

    monkeypatch.setattr(faceshield.asyncio, "create_subprocess_exec", create_process)
    adapter_one = FaceShieldAdapter(_config(repo))
    adapter_two = FaceShieldAdapter(_config(repo))

    async def run_both() -> list[bytes]:
        return await asyncio.gather(
            adapter_one.protect_png(INPUT_PNG), adapter_two.protect_png(INPUT_PNG)
        )

    assert asyncio.run(run_both()) == [OUTPUT_PNG, OUTPUT_PNG]
    assert maximum_active == 1


def test_protect_png_times_out_kills_process_group_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    process = HangingProcess()
    request_path: Path | None = None
    killed_groups: list[tuple[int, int]] = []

    async def create_process(*args: str, **_: Any) -> HangingProcess:
        nonlocal request_path
        request_path = Path(args[-1]).parent
        return process

    monkeypatch.setattr(faceshield.asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(faceshield.os, "killpg", lambda pid, sig: killed_groups.append((pid, sig)))

    with pytest.raises(FaceShieldTimeoutError, match="FACESHIELD_TIMEOUT"):
        asyncio.run(FaceShieldAdapter(_config(repo, timeout_seconds=0.001)).protect_png(INPUT_PNG))

    assert killed_groups == [(process.pid, faceshield.signal.SIGKILL)]
    assert process.returncode == -9
    assert request_path is not None and not request_path.exists()


@pytest.mark.parametrize("repo_exists", [False, True])
def test_protect_png_reports_unavailable_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo_exists: bool
) -> None:
    repo = _repo(tmp_path) if repo_exists else None

    async def unavailable_process(*_: str, **__: Any) -> FakeProcess:
        raise FileNotFoundError

    monkeypatch.setattr(faceshield.asyncio, "create_subprocess_exec", unavailable_process)

    with pytest.raises(FaceShieldUnavailableError, match="FACESHIELD_UNAVAILABLE"):
        asyncio.run(FaceShieldAdapter(_config(repo)).protect_png(INPUT_PNG))


def test_protect_png_maps_only_configured_no_face_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)

    async def create_process(*_: str, **__: Any) -> FakeProcess:
        return FakeProcess(returncode=20)

    monkeypatch.setattr(faceshield.asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(FaceShieldNoFaceDetectedError, match="NO_FACE_DETECTED"):
        asyncio.run(
            FaceShieldAdapter(_config(repo, no_face_exit_codes=frozenset({20}))).protect_png(
                INPUT_PNG
            )
        )


@pytest.mark.parametrize("returncode", [0, 1])
def test_protect_png_reports_execution_failures_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
) -> None:
    repo = _repo(tmp_path)
    request_path: Path | None = None

    async def create_process(*args: str, **_: Any) -> FakeProcess:
        nonlocal request_path
        request_path = Path(args[-1]).parent
        return FakeProcess(returncode=returncode)

    monkeypatch.setattr(faceshield.asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(FaceShieldExecutionError, match="FACESHIELD_EXECUTION_FAILED") as exc_info:
        asyncio.run(FaceShieldAdapter(_config(repo)).protect_png(INPUT_PNG))

    expected_returncode = returncode if returncode else None
    assert exc_info.value.returncode == expected_returncode
    assert request_path is not None and not request_path.exists()


def test_protect_png_rejects_non_png_before_starting_process(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="PNG bytes"):
        asyncio.run(FaceShieldAdapter(_config(_repo(tmp_path))).protect_png(b"not a png"))
