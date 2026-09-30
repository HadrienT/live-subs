"""Where the ahead-of-live audio comes from: yt-dlp at the live edge, piped
through ffmpeg into 16 kHz mono PCM. A fake source serves the tests."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
import shutil
from collections.abc import AsyncIterator
from typing import Protocol

import httpx
import numpy as np
from numpy.typing import NDArray

log = logging.getLogger(__name__)

SR = 16_000
CHUNK = 1_600  # 100 ms
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class PcmSource(Protocol):
    # Replay sources may only start at a boundary (a DASH fragment): where the
    # first sample really is, in seconds of the video.
    actual_start_s: float

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
        self.actual_start_s = 0.0

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

    def __init__(
        self, audio: NDArray[np.float32], *, speed: float = 1.0, start_s: float = 0.0
    ) -> None:
        self.audio = audio
        self.speed = speed
        self.actual_start_s = start_s

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


# --------------------------------------------------------------------------- replays


class FfmpegUrlSource:
    """Audio of a replay (VOD) from ``start_s``, decoded as fast as it is consumed.

    ffmpeg seeks with HTTP range requests, so starting anywhere is instant; the
    controller throttles reading (pipe back-pressure) to stay a bounded lead ahead
    of the player."""

    def __init__(self, audio_url: str, start_s: float, *, ffmpeg: str = "ffmpeg") -> None:
        path = shutil.which(ffmpeg)
        if path is None:
            raise SourceError("ffmpeg not found: ahead mode needs it")
        self._args = [
            path,
            "-nostdin",
            "-loglevel",
            "error",
            "-ss",
            f"{max(start_s, 0.0):.3f}",
            "-i",
            audio_url,
            "-ac",
            "1",
            "-ar",
            str(SR),
            "-f",
            "s16le",
            "pipe:1",
        ]
        self._proc: asyncio.subprocess.Process | None = None
        self.actual_start_s = max(start_s, 0.0)

    async def chunks(self) -> AsyncIterator[NDArray[np.float32]]:
        self._proc = await asyncio.create_subprocess_exec(
            *self._args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        assert self._proc.stdout is not None
        while True:
            try:
                data = await self._proc.stdout.readexactly(CHUNK * 2)
            except asyncio.IncompleteReadError as e:
                if e.partial:
                    yield np.frombuffer(e.partial, dtype="<i2").astype(np.float32) / 32768.0
                return  # end of the replay
            yield np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0

    async def aclose(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            self._proc.kill()
            await self._proc.wait()


class DashFragmentSource:
    """A replay YouTube has not re-encoded yet (``post_live``): only DASH fragments
    of ~2 s, each a standalone MP4 (its own ftyp+moov), no single seekable file.
    Start at the fragment holding ``start_s`` and decode fragment after fragment."""

    def __init__(
        self, fragment_urls: list[str], duration_s: float, start_s: float, *, ffmpeg: str = "ffmpeg"
    ) -> None:
        path = shutil.which(ffmpeg)
        if path is None:
            raise SourceError("ffmpeg not found: ahead mode needs it")
        if not fragment_urls or duration_s <= 0:
            raise SourceError("no DASH fragments for this replay")
        self._ffmpeg = path
        self._urls = fragment_urls
        self._seg_s = duration_s / len(fragment_urls)
        self._first = min(int(max(start_s, 0.0) // self._seg_s), len(fragment_urls) - 1)
        self.actual_start_s = self._first * self._seg_s
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
        self._prefetch: asyncio.Future[httpx.Response] | None = None

    async def _decode(self, data: bytes) -> NDArray[np.float32]:
        proc = await asyncio.create_subprocess_exec(
            self._ffmpeg,
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-ac",
            "1",
            "-ar",
            str(SR),
            "-f",
            "s16le",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate(data)
        if proc.returncode != 0:
            raise SourceError(f"ffmpeg: {err.decode('utf-8', 'replace').strip()[-200:]}")
        return np.frombuffer(out, dtype="<i2").astype(np.float32) / 32768.0

    async def chunks(self) -> AsyncIterator[NDArray[np.float32]]:
        next_fetch: asyncio.Future[httpx.Response] | None = None
        for i in range(self._first, len(self._urls)):
            resp = (
                await next_fetch
                if next_fetch is not None
                else await self._client.get(self._urls[i])
            )
            if i + 1 < len(self._urls):  # fetch the next fragment while this one decodes
                next_fetch = self._prefetch = asyncio.ensure_future(
                    self._client.get(self._urls[i + 1])
                )
            if resp.status_code != 200:
                raise SourceError(f"DASH fragment {i}: HTTP {resp.status_code}")
            audio = await self._decode(resp.content)
            for off in range(0, len(audio), CHUNK):
                yield audio[off : off + CHUNK]

    async def aclose(self) -> None:
        if self._prefetch is not None:
            self._prefetch.cancel()
        await self._client.aclose()


class Resolver(Protocol):
    """What the video is (live or replay) and how to read its audio."""

    async def probe(self) -> bool:
        """True for a live broadcast in progress, False for a replay."""
        ...

    def live_source(self) -> PcmSource: ...

    def replay_source(self, start_s: float) -> PcmSource: ...


class YouTubeResolver:
    def __init__(self, video_id: str, *, ytdlp: str = "yt-dlp", ffmpeg: str = "ffmpeg") -> None:
        self.video_id = video_id
        self.url = watch_url(video_id)
        self._ytdlp = shutil.which(ytdlp)
        self._ffmpeg = ffmpeg
        if self._ytdlp is None or shutil.which(ffmpeg) is None:
            raise SourceError("yt-dlp or ffmpeg not found: ahead mode needs both")
        self._audio_url: str | None = None
        self._fragments: list[str] = []
        self._duration = 0.0

    async def probe(self) -> bool:
        assert self._ytdlp is not None
        proc = await asyncio.create_subprocess_exec(
            self._ytdlp,
            "--no-warnings",
            "-J",
            "-f",
            "bestaudio/best",
            self.url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise SourceError(f"yt-dlp: {err.decode('utf-8', 'replace').strip()[-300:]}")
        info = json.loads(out)
        # "was_live" / "post_live" / "not_live": the whole recording exists.
        live = info.get("live_status") == "is_live" or info.get("is_live") is True
        if not live:
            fragments = info.get("fragments") or []
            if info.get("protocol") == "http_dash_segments" and fragments:
                # post_live: not re-encoded yet, only standalone ~2 s DASH fragments
                base = info.get("fragment_base_url") or ""
                self._fragments = [f.get("url") or base + f["path"] for f in fragments]
                self._duration = float(info.get("duration") or 0)
            else:
                self._audio_url = info.get("url") or next(
                    (f["url"] for f in info.get("requested_formats") or [] if f.get("url")), None
                )
                if not self._audio_url:
                    raise SourceError("yt-dlp gave no audio URL for this replay")
        log.info("ahead: %s is %s", self.video_id, "live" if live else "a replay")
        return bool(live)

    def live_source(self) -> PcmSource:
        return YtDlpSource(self.video_id, ffmpeg=self._ffmpeg)

    def replay_source(self, start_s: float) -> PcmSource:
        if self._fragments:
            return DashFragmentSource(self._fragments, self._duration, start_s, ffmpeg=self._ffmpeg)
        assert self._audio_url is not None
        return FfmpegUrlSource(self._audio_url, start_s, ffmpeg=self._ffmpeg)
