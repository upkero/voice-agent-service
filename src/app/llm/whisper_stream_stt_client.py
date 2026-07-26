"""Adapter: a self-hosted WhisperLive server behind the project's STT port, streaming.

This is the "build your own streaming STT" path. The naive way to fake streaming
on a batch API is to slice the audio and transcribe the pieces independently —
which loses the acoustic context across cuts and mangles boundary words.
WhisperLive avoids that with a rolling buffer plus LocalAgreement: it keeps a
growing window, re-transcribes the whole window, and only commits words two
consecutive passes agree on. So the streaming is real, and the context is intact.

The trade-off is honest and lives in the deployment, not the code: LocalAgreement
re-transcribes continuously, so commit latency tracks the host's compute. On a
GPU it is ~1s; on a small CPU it can be slower than batch. That is why this runs
as its own service (see docker-compose profile `selfhost-stt`) reached by URL,
and belongs on a GPU instance in production — the same peer-service shape as
ops-core-api.

Verified against the WhisperLive JSON/binary WebSocket protocol; the live
handshake needs a running server (`docker compose --profile selfhost-stt up`).
"""

import asyncio
import json
from logging import getLogger
from typing import Any
from uuid import uuid4

import numpy as np
import websockets
from livekit.agents import APIConnectionError, NotGivenOr, stt, utils
from livekit.agents.language import LanguageCode
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, APIConnectOptions
from websockets.asyncio.client import ClientConnection

from src.app.core.settings.stt import STTSettings
from src.app.interfaces.llm.stt_client import STTClient

logger = getLogger(__name__)

# WhisperLive consumes 16 kHz mono float32. The base SpeechStream is told this
# rate so livekit resamples every incoming frame before _run sees it.
_WHISPERLIVE_SAMPLE_RATE = 16000


class WhisperStreamSTTClient(STTClient):
    def __init__(self, settings: STTSettings, language: str) -> None:
        # streaming=True is the whole point: it puts the session on the live
        # path (stream()) instead of the VAD-buffered recognize() path.
        super().__init__(capabilities=stt.STTCapabilities(streaming=True, interim_results=True))
        self._settings = settings
        self._language = settings.language or language
        if not settings.whisper_stream_url:
            raise ValueError("STT_WHISPER_STREAM_URL is required for the whisper_stream provider.")
        self._url = settings.whisper_stream_url
        # WhisperLive names its own model set (tiny..large-v3). "small" from the
        # faster_whisper default carries over sensibly.
        self._model = settings.model

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        # A streaming STT still has to answer the one-shot method; the session
        # never calls it for a streaming client, but the interface requires it.
        raise NotImplementedError("WhisperStreamSTTClient is streaming; use stream().")

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "_WhisperLiveStream":
        return _WhisperLiveStream(self, self._url, self._model, self._language, conn_options)


class _WhisperLiveStream(stt.SpeechStream):
    def __init__(
        self,
        client: WhisperStreamSTTClient,
        url: str,
        model: str,
        language: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(stt=client, conn_options=conn_options, sample_rate=_WHISPERLIVE_SAMPLE_RATE)
        self._url = url
        self._model = model
        self._language = language
        self._uid = str(uuid4())
        # How many committed segments we have already turned into final events,
        # so a message that repeats the backlog does not re-emit them.
        self._finalized = 0

    async def _run(self) -> None:
        try:
            # ping_interval=None: WhisperLive loads the Whisper model on the first
            # connection (slow on CPU, and it blocks the server's thread), so the
            # default client keepalive would tear the socket down mid-load before
            # SERVER_READY ever arrives. A GPU server is fast, but a cold or busy
            # one must not be killed by our own ping timer.
            async with websockets.connect(self._url, max_size=None, ping_interval=None) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "uid": self._uid,
                            "language": self._language,
                            "task": "transcribe",
                            "model": self._model,
                            "use_vad": True,
                        }
                    )
                )
                await self._await_ready(ws)
                # Send and receive concurrently: audio must keep flowing while
                # transcripts come back, or the buffer stalls.
                await asyncio.gather(self._send_audio(ws), self._receive(ws))
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            # Surfaced as an APIError so the session's retry / FallbackAdapter can
            # react, instead of dying as a bare socket error.
            raise APIConnectionError(f"WhisperLive connection failed: {exc}") from exc

    async def _await_ready(self, ws: ClientConnection) -> None:
        async for message in ws:
            try:
                data = json.loads(message)
            except (TypeError, ValueError):
                continue
            if data.get("message") == "SERVER_READY":
                return
            if data.get("status") == "ERROR" or data.get("message") == "ERROR":
                raise APIConnectionError(f"WhisperLive rejected the session: {data}")
        raise APIConnectionError("WhisperLive closed before it was ready.")

    async def _send_audio(self, ws: ClientConnection) -> None:
        async for frame in self._input_ch:
            if isinstance(frame, self._FlushSentinel):
                continue
            # livekit delivers 16-bit PCM; WhisperLive wants float32 in [-1, 1].
            samples = np.frombuffer(frame.data.tobytes(), dtype=np.int16).astype(np.float32) / 32768.0
            await ws.send(samples.tobytes())

    async def _receive(self, ws: ClientConnection) -> None:
        async for message in ws:
            try:
                data = json.loads(message)
            except (TypeError, ValueError):
                continue
            if data.get("uid") not in (self._uid, None):
                continue
            segments = data.get("segments")
            if segments:
                self._emit_segments(segments)

    def _emit_segments(self, segments: list[dict[str, Any]]) -> None:
        """Turn WhisperLive's rolling segment list into interim/final events.

        WhisperLive resends a window of recent segments each time; a segment
        carries a 'completed' flag once LocalAgreement has committed it. We emit
        each newly completed segment once as a final, and the tail of not-yet-
        committed text as a single interim.

        Verified against a live WhisperLive server: a segment flips to
        'completed' only once the *next* utterance begins, so the last thing a
        caller says stays interim until they speak again. That is expected, not a
        gap — the AgentSession commits the turn from the latest interim via its
        own VAD endpointing (min/max_endpointing_delay in session.py), exactly as
        it does for any streaming STT. The final is a fast-path, not the only path.
        """
        completed = [s for s in segments if s.get("completed")]
        for segment in completed[self._finalized :]:
            text = str(segment.get("text", "")).strip()
            if text:
                self._event_ch.send_nowait(
                    stt.SpeechEvent(
                        type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                        alternatives=[stt.SpeechData(language=LanguageCode(self._language), text=text)],
                    )
                )
        self._finalized = max(self._finalized, len(completed))

        pending = " ".join(str(s.get("text", "")).strip() for s in segments if not s.get("completed")).strip()
        if pending:
            self._event_ch.send_nowait(
                stt.SpeechEvent(
                    type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                    alternatives=[stt.SpeechData(language=LanguageCode(self._language), text=pending)],
                )
            )
