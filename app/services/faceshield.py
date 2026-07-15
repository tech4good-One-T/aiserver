"""Asynchronous adapter for an externally installed FaceShield checkout.

The official FaceShield project is intentionally not imported by this process: it
uses a different Python/CUDA environment.  This adapter gives it a request-scoped
PNG file, invokes its CLI without a shell, and returns the generated PNG bytes.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import Settings

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_RUN_LOCK = asyncio.Lock()
_SECRET_ENV_NAMES = frozenset(
    {
        "GEMINI_API_KEY",
        "API_GEMINI_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "UPSTAGE_API_KEY",
        "OPENAI_API_KEY",
        "HF_TOKEN",
        "HUGGINGFACE_HUB_TOKEN",
        "DATABASE_URL",
        "SECRET_KEY",
        "JWT_SECRET",
    }
)


class FaceShieldError(RuntimeError):
    """Base class for expected FaceShield adapter failures."""

    code = "FACESHIELD_ERROR"

    def __str__(self) -> str:
        """Avoid including commands, temporary paths, or image data in logs."""
        return self.code


class FaceShieldUnavailableError(FaceShieldError):
    """The configured repository or executable is unavailable."""

    code = "FACESHIELD_UNAVAILABLE"


class FaceShieldTimeoutError(FaceShieldError):
    """FaceShield did not finish before its configured deadline."""

    code = "FACESHIELD_TIMEOUT"


class FaceShieldExecutionError(FaceShieldError):
    """FaceShield exited unsuccessfully or did not create a valid result."""

    code = "FACESHIELD_EXECUTION_FAILED"

    def __init__(self, returncode: int | None = None) -> None:
        super().__init__()
        self.returncode = returncode


class FaceShieldNoFaceDetectedError(FaceShieldError):
    """A configured wrapper explicitly reported that no face was detected."""

    code = "NO_FACE_DETECTED"


@dataclass(frozen=True, slots=True)
class FaceShieldConfig:
    """Runtime-only FaceShield CLI configuration.

    ``command`` is an argv prefix, never a shell command.  Its default invokes the
    official ``execute.sh`` through the project's documented conda environment.
    The ten official positional arguments are appended by the adapter.

    The upstream repository currently does not expose a reliable no-face signal.
    A deployment-specific wrapper may use one of ``no_face_exit_codes`` to add one;
    an absent output is deliberately treated as an execution failure instead.
    """

    repo_path: Path | None
    command: tuple[str, ...] = (
        "conda",
        "run",
        "--no-capture-output",
        "-n",
        "faceshield",
        "bash",
        "execute.sh",
    )
    timeout_seconds: float = 600.0
    resize_shape: int = 512
    projection_loss: str = "l1"
    attention_loss: str = "l2"
    attention_threshold: float = 0.2
    arcface_loss: str = "cosine"
    total_iterations: int = 30
    noise_clamp: int = 12
    step_size: int = 1
    output_pattern: str = "**/protected.png"
    no_face_exit_codes: frozenset[int] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("FaceShield command must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("FaceShield timeout must be positive")
        if self.resize_shape <= 0 or self.total_iterations <= 0:
            raise ValueError("FaceShield dimensions and iteration count must be positive")
        if self.noise_clamp <= 0 or self.step_size <= 0:
            raise ValueError("FaceShield noise parameters must be positive")
        if self.projection_loss not in {"l1", "l2"}:
            raise ValueError("Unsupported FaceShield projection loss")
        if self.attention_loss not in {"l1", "l2"}:
            raise ValueError("Unsupported FaceShield attention loss")
        if self.arcface_loss not in {"cosine", "l2"}:
            raise ValueError("Unsupported FaceShield ArcFace loss")
        if not 0 <= self.attention_threshold <= 1:
            raise ValueError("FaceShield attention threshold must be between zero and one")

    @classmethod
    def from_settings(cls, settings: Settings) -> FaceShieldConfig:
        """Create an adapter config from the application's environment settings."""
        command = tuple(shlex.split(settings.faceshield_command))
        return cls(
            repo_path=settings.faceshield_repo_path,
            command=command,
            timeout_seconds=float(settings.processing_timeout_seconds),
        )


class FaceShieldAdapter:
    """Serialize and execute FaceShield protection jobs in this worker process."""

    def __init__(self, config: FaceShieldConfig) -> None:
        self._config = config

    async def protect_png(self, image: bytes) -> bytes:
        """Apply FaceShield to normalized PNG bytes and return a protected PNG."""
        if not image.startswith(_PNG_SIGNATURE):
            raise ValueError("FaceShield input must be PNG bytes")

        async with _RUN_LOCK:
            return await self._protect_png_exclusive(image)

    async def _protect_png_exclusive(self, image: bytes) -> bytes:
        repo_path = self._config.repo_path
        if repo_path is None or not repo_path.is_dir():
            raise FaceShieldUnavailableError

        try:
            temporary_directory = tempfile.TemporaryDirectory(prefix="faceshield-request-")
        except OSError as exc:
            raise FaceShieldUnavailableError from exc

        with temporary_directory as request_directory:
            request_path = Path(request_directory)
            input_path = request_path / "input.png"
            output_path = request_path / "output"
            output_path.mkdir()
            try:
                await asyncio.to_thread(input_path.write_bytes, image)
            except OSError as exc:
                raise FaceShieldExecutionError from exc

            command = self._build_command(input_path, output_path)
            child_environment = os.environ.copy()
            for secret_name in _SECRET_ENV_NAMES:
                child_environment.pop(secret_name, None)
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=repo_path,
                    env=child_environment,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                )
            except (FileNotFoundError, PermissionError, OSError) as exc:
                raise FaceShieldUnavailableError from exc

            try:
                returncode = await asyncio.wait_for(
                    process.wait(), timeout=self._config.timeout_seconds
                )
            except TimeoutError as exc:
                await self._terminate_process(process)
                raise FaceShieldTimeoutError from exc
            except BaseException:
                await self._terminate_process(process)
                raise

            if returncode in self._config.no_face_exit_codes:
                raise FaceShieldNoFaceDetectedError
            if returncode != 0:
                raise FaceShieldExecutionError(returncode)

            return await self._read_result(output_path)

    def _build_command(self, input_path: Path, output_path: Path) -> tuple[str, ...]:
        return (
            *self._config.command,
            str(output_path),
            str(self._config.resize_shape),
            self._config.projection_loss,
            self._config.attention_loss,
            str(self._config.attention_threshold),
            self._config.arcface_loss,
            str(self._config.total_iterations),
            str(self._config.noise_clamp),
            str(self._config.step_size),
            str(input_path),
        )

    async def _read_result(self, output_path: Path) -> bytes:
        try:
            candidates = [
                candidate
                for candidate in output_path.glob(self._config.output_pattern)
                if candidate.is_file() and not candidate.is_symlink()
            ]
        except (OSError, ValueError) as exc:
            raise FaceShieldExecutionError from exc

        if len(candidates) != 1:
            raise FaceShieldExecutionError

        try:
            protected = await asyncio.to_thread(candidates[0].read_bytes)
        except OSError as exc:
            raise FaceShieldExecutionError from exc
        if not protected.startswith(_PNG_SIGNATURE):
            raise FaceShieldExecutionError
        return protected

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return

        with suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(process.pid, signal.SIGKILL)
        with suppress(ProcessLookupError):
            process.kill()
        with suppress(TimeoutError, ProcessLookupError):
            await asyncio.wait_for(process.wait(), timeout=5)
