"""Where the ahead-of-live audio comes from: yt-dlp at the live edge, piped
through ffmpeg into 16 kHz mono PCM. A fake source serves the tests."""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
import shutil
from collections.abc import AsyncIterator
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

log = logging.getLogger(__name__)

SR = 16_000
CHUNK = 1_600  # 100 ms
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class PcmSource(Protocol):
    def chunks(self) -> AsyncIterator[NDArray[np.float32]]: ...

    async def aclose(self) -> None: ...


class SourceError(RuntimeError):
    pass


def watch_url(video_id: str) -> str:
    """Only a YouTube video id is accepted, never a client-supplied URL."""
    if not _VIDEO_ID.match(video_id):
        raise SourceError(f"not a YouTube video id: {video_id!r}")
    return f"https://www.youtube.com/watch?v={video_id}"


class YtDlpSource:
    """``yt-dlp -o - <live> | ffmpeg → s16le``. yt-dlp starts at the live edge."""

    def __init__(self, video_id: str, *, ytdlp: str = "yt-dlp", ffmpeg: str = "ffmpeg") -> None:
        self.url = watch_url(video_id)
        found = {tool: shutil.which(tool) for tool in (ytdlp, ffmpeg)}
        missing = [tool for tool, path in found.items() if path is None]
        if missing:
            raise SourceError(f"{', '.join(missing)} not found: ahead mode needs yt-dlp and ffmpeg")
        ytdlp, ffmpeg = str(found[ytdlp]), str(found[ffmpeg])  # --ffmpeg-location wants a path
        self._cmd = (
            # Lives are HLS: yt-dlp needs ffmpeg itself to write them to stdout.
            f"{shlex.quote(ytdlp)} --quiet --no-warnings --ffmpeg-location {shlex.quote(ffmpeg)} "
            f"-f bestaudio/best -o - {shlex.quote(self.url)} "
            f"| {shlex.quote(ffmpeg)} -nostdin -loglevel error "
            f"-i pipe:0 -ac 1 -ar {SR} -f s16le pipe:1"
        )
        self._proc: asyncio.subprocess.Process | None = None

    async def chunks(self) -> AsyncIterator[NDArray[np.float32]]:
        self._proc = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            self._cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self._proc.stdout is not None
        while True:
            try:
                data = await self._proc.stdout.readexactly(CHUNK * 2)
            except asyncio.IncompleteReadError as e:
                err = b""
                if self._proc.stderr is not None:
                    err = await self._proc.stderr.read()
                raise SourceError(
                    f"live stream ended: {err.decode('utf-8', 'replace')[-300:]}"
                ) from e
            yield np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0

    async def aclose(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            self._proc.kill()
            await self._proc.wait()


class ArraySource:
    """Plays an array in real time (``speed`` ×): tests and offline trials."""

    def __init__(self, audio: NDArray[np.float32], *, speed: float = 1.0) -> None:
        self.audio = audio
        self.speed = speed

    async def chunks(self) -> AsyncIterator[NDArray[np.float32]]:
        loop = asyncio.get_running_loop()
        start = loop.time()
        for i, off in enumerate(range(0, len(self.audio), CHUNK)):
            if self.speed > 0:
                delay = start + i * CHUNK / SR / self.speed - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
            yield self.audio[off : off + CHUNK]

    async def aclose(self) -> None:
        pass
