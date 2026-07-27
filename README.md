# voice-agent-service

[![CI](https://github.com/upkero/voice-agent-service/actions/workflows/ci.yml/badge.svg)](https://github.com/upkero/voice-agent-service/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

A real LiveKit voice agent that books restaurant tables by phone. Persona: **Мила**, a
receptionist for a fictional restaurant. She listens, speaks, and calls typed tools to check
availability and take a booking against [`ops-core-api`](../ops-core-api) — a genuine WebRTC
voice pipeline (LiveKit transport, streaming STT → LLM → TTS, deterministic tool-calling), not a
browser Web-Speech imitation.

Offline-first: speech recognition (faster-whisper) and speech synthesis (piper) both run locally,
and LiveKit runs in development mode. `docker compose up` needs **no cloud account and no API key
for speech** — only the key of the ops-core-api instance holding the booking data.

*[Русская версия ниже](#voice-agent-service-русская-версия).*

---

## What it does

| Capability | Detail |
|---|---|
| Voice pipeline | LiveKit WebRTC transport, Silero VAD, STT → LLM → TTS via `AgentSession`. |
| Deterministic tools | Four function tools with explicit JSON Schema + Pydantic validation before dispatch, so a mishearing cannot take a table. |
| Booking | Checks availability, offers the nearest real slots, books, finds and cancels — all against ops-core-api over HTTP. |
| Bilingual | `AGENT_LANGUAGE=ru\|en` switches the Whisper hint, the piper voice and the persona in one setting. |
| Graceful degradation | ops-core-api down, or STT/TTS unavailable, produces a spoken or data-channel explanation — never silence or a crash. |

Two processes live in this repo:

1. **HTTP API** (`src/main.py`) — a thin token server: `GET /health/*` and `POST /token`, which
   returns a LiveKit join JWT so a browser can connect to a room directly over WebRTC. No booking
   logic lives here.
2. **Agent worker** (`src/entrypoint.py`) — the voice agent itself. Connects to the room, runs
   the pipeline, and exposes the booking tools to the LLM.

## Architecture

Layered, strict inward dependency rule. Two delivery layers (`api/v1/` and `voice/`) because it
runs as two processes; no `models/` or `db/` because it owns no database — its only data source is
ops-core-api. Full description and the patterns table in [`docs/architecture.md`](docs/architecture.md).

```
        api/v1  (HTTP)   │   voice  (LiveKit worker)      ← delivery
                    services  (business logic)
        repositories     │   llm  (stt / tts / dialogue)
                    interfaces  (ABC ports)
                    contracts  (frozen dataclasses)
              core / exceptions / bootstrap
```

| Pattern | Where |
|---|---|
| **Adapter** | HTTP client to ops-core-api behind the `BookingRepository` port; local model/binary behind livekit's STT/TTS. |
| **Factory** | One constructor each for the dialogue LLM, the STT client and the TTS client — provider chosen by settings. |
| **Strategy** | Slot ranking (nearest-time vs earliest-first), injected into `ReservationService`. |
| **Template Method** | The dialogue system prompt: fixed section order, overridable steps. |

## Quick start

```bash
cp .env.example .env
# set CORE_API_API_KEY to your ops-core-api key (the .env.example ships a dev one)
docker compose up --build
```

That brings up three containers:

| Service | Port | Role |
|---|---|---|
| `livekit` | 7880 / 7881 / 7882 | LiveKit server in `--dev` mode (issues the `devkey`/`secret` pair) |
| `api` | 8080 | Token + health HTTP server |
| `agent` | — | The voice worker; registers with LiveKit and waits for a call |

The agent image bakes in the piper binary, both voices and the Whisper model at build time, so a
cold start pulls nothing.

**You still need a dialogue LLM and the booking backend running:**

- **ops-core-api** — start it from its own folder (`cd ../ops-core-api && docker compose up -d`).
  If it is down, the demo still connects and Мила explains the outage out loud — that is the
  degradation path, live.
- **An LLM with tool-calling** — the default `.env` points at a local Ollama
  (`LLM_MODEL=qwen2.5:7b`). Point `LLM_*` at any OpenAI-compatible endpoint instead.

## Endpoints

```bash
# Liveness — is the process up
curl http://localhost:8080/health/live
# {"status":"ok"}

# Readiness — can it reach the LiveKit server
curl http://localhost:8080/health/ready
# {"status":"ok"}   (503 if LiveKit is unreachable)

# Mint a join token (room name optional — a private one is generated if omitted)
curl -X POST http://localhost:8080/api/v1/token \
  -H 'content-type: application/json' \
  -d '{"participant_name": "demo", "room_name": "table-demo"}'
# {"token":"eyJ…","room_name":"table-demo","participant_name":"demo-x1y2z3",
#  "livekit_url":"ws://localhost:7880","expires_at":"…"}
```

## Talking to Мила

This repo ships no web page — a portfolio site will host the demo client. To try it now, connect
any LiveKit client to the room with the token above. The quickest is the
[LiveKit Agents Playground](https://agents-playground.livekit.io):

1. `POST /token` as above, and copy `token` + `livekit_url`.
2. Open the Playground, choose **Manual** connection, paste the URL and token, connect.
3. Say *"a table for four tomorrow at seven"* (or, with `AGENT_LANGUAGE=ru`,
   *"стол на четверых завтра в семь вечера"*).

Мила greets you, calls `check_availability`, offers the nearest real slots, reads your choice
back, and calls `create_booking` once you agree. The booking appears in ops-core-api:

```bash
curl 'http://localhost:8000/api/v1/booking-slots?date=<tomorrow>&resource_type=table' \
  -H 'X-API-Key: <your key>'   # the booked slot now reads "is_available": false
```

## Configuring speech (STT and TTS are independent)

Recognition and synthesis are two separate ports with two separate settings blocks, each with
several providers picked by one env var. Any combination is supported — local recognition with a
cloud voice, streaming STT with a batch TTS, and so on. **Whether a provider streams is not a
branch anywhere in the app**: each client declares its capability and `AgentSession` drives it,
wrapping a batch client in its own VAD segmenter and feeding a streaming one live.

| `STT_PROVIDER` | Mode | Latency after speech | Needs |
|---|---|---|---|
| `faster_whisper` | batch, local | ~1-2s (CPU) | nothing — offline default |
| `openai_compatible` | batch REST | ~2s/clip | `STT_BASE_URL` / `STT_API_KEY` / `STT_MODEL` (OpenRouter, OpenAI, …) |
| `deepgram` | **streaming** | ~150-300ms | a Deepgram key in `STT_API_KEY`, `STT_MODEL=nova-3` |
| `whisper_stream` | **streaming** | ~1s on GPU | `STT_WHISPER_STREAM_URL` → a self-hosted WhisperLive |

| `TTS_PROVIDER` | Mode | First byte | Needs |
|---|---|---|---|
| `piper` | local | ~150ms | nothing — offline default |
| `openai_compatible` | batch REST | ~2-3s | `TTS_BASE_URL` / `TTS_API_KEY` / `TTS_MODEL` / `TTS_VOICE` |
| `cartesia` | **streaming** | ~90ms | a Cartesia key in `TTS_API_KEY`, `TTS_MODEL=sonic-2`, a Cartesia `TTS_VOICE` |

The OpenAI-compatible clients are **provider-agnostic**: nothing in the code names a provider,
`*_BASE_URL` selects it, so the same client points at OpenRouter, OpenAI, or any server
implementing `/audio/transcriptions` and `/audio/speech`. OpenRouter is batch REST only — for true
streaming (near-zero STT latency, ~90ms TTS) use `deepgram` / `cartesia`, whose LiveKit plugins hit
the providers' native WebSockets.

### Fallback: prefer streaming, degrade gracefully

Set `STT_FALLBACK_PROVIDER` / `TTS_FALLBACK_PROVIDER` and the two providers are wrapped in a
`FallbackAdapter`: if the streaming primary fails at connect time, the session fails over to the
secondary per request. A production pairing is a streaming primary with a resilient fallback —
`STT_PROVIDER=deepgram` + `STT_FALLBACK_PROVIDER=openai_compatible`, or
`TTS_PROVIDER=cartesia` + `TTS_FALLBACK_PROVIDER=piper` so a provider outage drops to the offline
voice instead of to silence.

### Self-hosted streaming STT

`whisper_stream` talks to a [WhisperLive](https://github.com/collabora/WhisperLive) server, which
streams real transcripts (rolling buffer + LocalAgreement, so context survives across chunks — not
naive slicing). It is a **peer service reached by URL**, the same shape as ops-core-api:

```bash
docker compose --profile selfhost-stt up   # runs WhisperLive at ws://whisper:9090 locally
# then: STT_PROVIDER=whisper_stream, STT_WHISPER_STREAM_URL=ws://whisper:9090
```

In production it moves to its own instance unchanged (just a different URL). Its latency tracks the
host: ~1s on a GPU, slower than batch on a small CPU — so it belongs on a GPU box, while Deepgram
gives the same streaming latency with no infra.

### Adding a provider that is *not* OpenAI-compatible

Cartesia has its own request shape — being audio does not make it OpenAI-shaped. It is not a
variant of the OpenAI-compatible client but its own selectable provider: one branch in
`llm/tts_factory.py` (its LiveKit plugin already satisfies the `tts.TTS` port, so no wrapper) plus
`"cartesia"` in the `TTSProvider` literal. Nothing in `services/`, `voice/` or `api/` changes —
grep the tree for `cartesia` and it appears only in the factory and settings. That is Open/Closed,
and it is checkable.

## How degradation behaves

| Failure | What the guest gets |
|---|---|
| ops-core-api down / 5xx | A spoken sentence ("I can't reach the diary right now — shall I take your number?"). No exception reaches the pipeline. |
| TTS provider fails | The reply text is published to the room's data channel and the problem is announced once. The session stays up. |
| STT provider fails | Мила says she cannot hear (TTS still works); text input stays enabled, so the same LLM + tools loop works by typing. |
| Both fail at start | The reason is published to the data channel and logged, instead of an empty room. |

## Design notes worth reading

**Deterministic tool-calling.** Every tool has an explicit, closed JSON Schema
(`additionalProperties: false`, everything `required`, patterns on dates/times, bounds on party
size) *and* a Pydantic model that validates before dispatch. The schema is a request to the
provider; the Pydantic layer is the enforcement, because providers honour schemas to varying
degrees and "mostly" is not a property to book a table on.

**Identifiers never reach the model.** A UUID is unspeakable and a model can mistype one, so the
LLM only ever sees short references (`slot_1`, `booking_1`) that this call's session resolves to
real IDs. A reference the session never issued cannot resolve, so a hallucinated identifier fails
locally instead of reaching ops-core-api. A booking made during the call is cancellable by its
reference with no second lookup.

**Duplicate and orphaned bookings.** Booking is idempotent: the key is derived from the intent
(`sha256(room:intent_seq:slot:name:party)`), so a network retry or a repeated tool call replays
the one booking instead of taking a second table. Cancelling bumps `intent_seq`, so a guest can
rebook the same table immediately without the consumed key being refused. The one orphan the key
cannot prevent — the call drops between the booking and the spoken confirmation — is logged with
`confirmation_spoken: false` rather than auto-cancelled, because releasing a table the guest
agreed to over one lost sentence is the worse failure.

## Tests

```bash
uv sync
uv run ruff check .
uv run mypy src
uv run pytest --cov=src/app/services --cov-report=term-missing
```

104 tests, ~94% coverage on the service layer. No database and no network: the `BookingRepository`
port is replaced with an in-memory fake (its second implementation), so the whole suite runs
offline. CI runs the same four commands on every push.

### Pre-flight: check the live providers

The unit suite runs offline against fakes. To prove the *configured* LLM/STT/TTS actually work —
before trying a voice call — run the provider check. It synthesises a sentence with the real TTS,
feeds that audio straight back into the real STT, and sends the real tool schemas to the LLM, so a
green run means the whole audio round-trip and tool-calling work end to end, no microphone needed:

```bash
uv run python -m scripts.check_providers
```

```
PASS spoke "Do you have a table for four?" -> 104,160 bytes (~2.2s at 24000 Hz)
PASS heard back: "Do you have a table for four?"
PASS model called: check_availability  args={"booking_date":"2026-07-24","party_size":4,...}
```

### Talking to her without a browser

`python -m src.entrypoint console` runs the agent in the terminal against your local microphone
and speakers — no LiveKit server, no token, no browser. The fastest way to actually hear her.
(Run it on the host, not in Docker: a container has no audio devices. Booking still needs
ops-core-api up; without it she says she can't reach the diary — the degradation path, live.)

## Out of scope (by choice)

Named so they read as decisions, not oversights: no barge-in tuning beyond Silero's defaults; no
call recording or transcript persistence (nothing here owns a database); no per-room worker
concurrency limits; no `openai_compatible` LLM streaming tuning beyond the plugin defaults.

## Running on Docker Desktop (Windows / macOS)

LiveKit media uses ICE, and in this topology two peers reach the server on two different networks:
the browser on `localhost`, the agent on the `livekit` service name. `config/livekit.yaml` sets
`use_external_ip: false` and publishes the TCP fallback port (7881), so a call connects over TCP
even when UDP is blocked. If media still fails, run the worker on the host against the
containerised server:

```bash
uv run python -m src.entrypoint dev   # reads .env; set LIVEKIT_URL=ws://localhost:7880
```

---

# voice-agent-service (русская версия)

Настоящий голосовой агент на **LiveKit**, который бронирует столики в ресторане по телефону.
Персона — **Мила**, администратор вымышленного ресторана: слышит, говорит и вызывает строго
типизированные инструменты, чтобы проверить наличие мест и оформить бронь в
[`ops-core-api`](../ops-core-api). Это полноценный WebRTC-пайплайн (LiveKit-транспорт,
потоковый STT → LLM → TTS, детерминированный tool-calling), а не имитация через браузерный
Web Speech API.

**Offline-first:** распознавание речи (faster-whisper) и синтез (piper) работают локально, LiveKit
поднимается в dev-режиме. Для `docker compose up` **не нужен ни облачный аккаунт, ни ключ для
речи** — только ключ того экземпляра ops-core-api, где лежат данные бронирований.

## Что делает

| Возможность | Детали |
|---|---|
| Голосовой пайплайн | LiveKit WebRTC, Silero VAD, STT → LLM → TTS через `AgentSession`. |
| Детерминированные инструменты | Четыре function-tool со строгой JSON Schema и валидацией Pydantic до вызова — ослышка не приводит к брони. |
| Бронирование | Проверка наличия, ближайшие реальные слоты, бронь, поиск и отмена — всё через HTTP к ops-core-api. |
| Двуязычность | `AGENT_LANGUAGE=ru\|en` переключает подсказку Whisper, голос piper и персону одной настройкой. |
| Грациозная деградация | Недоступность ops-core-api или STT/TTS даёт голосовое или текстовое объяснение — не тишину и не падение. |

Два процесса в одном репозитории:

1. **HTTP API** (`src/main.py`) — тонкий сервер токенов: `GET /health/*` и `POST /token`,
   возвращающий LiveKit-JWT, чтобы браузер подключился к комнате напрямую через WebRTC. Бизнес-логики
   бронирования тут нет.
2. **Agent worker** (`src/entrypoint.py`) — сам голосовой агент: подключается к комнате, крутит
   пайплайн и отдаёт инструменты бронирования модели.

## Архитектура

Слоистая, строгое правило зависимостей внутрь. Два слоя доставки (`api/v1/` и `voice/`), потому что
это два процесса; нет `models/` и `db/`, потому что своей базы нет — единственный источник данных
ops-core-api. Подробно и таблица паттернов — в [`docs/architecture.md`](docs/architecture.md).

| Паттерн | Где |
|---|---|
| **Adapter** | HTTP-клиент к ops-core-api за портом `BookingRepository`; локальная модель/бинарь за STT/TTS livekit. |
| **Factory** | По одному конструктору на диалоговый LLM, STT- и TTS-клиент — провайдер выбирается настройками. |
| **Strategy** | Ранжирование слотов (ближайшее время / раньше всех), внедряется в `ReservationService`. |
| **Template Method** | Системный промпт диалога: фиксированный порядок секций, переопределяемые шаги. |

## Быстрый старт

```bash
cp .env.example .env
# укажите CORE_API_API_KEY — ключ вашего ops-core-api (в .env.example лежит dev-значение)
docker compose up --build
```

Поднимаются три контейнера: `livekit` (dev-режим, пара `devkey`/`secret`), `api` (порт 8080,
токены и health) и `agent` (голосовой воркер, регистрируется в LiveKit и ждёт звонка). Образ агента
на этапе сборки вшивает бинарь piper, оба голоса и модель Whisper, поэтому холодный старт ничего не
качает.

**Дополнительно нужны LLM и бэкенд бронирований:**

- **ops-core-api** — запустите из его папки (`cd ../ops-core-api && docker compose up -d`). Если он
  недоступен, демо всё равно подключается, и Мила объясняет проблему голосом — это и есть проверка
  деградации вживую.
- **LLM с tool-calling** — по умолчанию `.env` смотрит на локальный Ollama (`qwen2.5:7b`). Можно
  направить `LLM_*` на любой OpenAI-совместимый эндпоинт.

## Эндпоинты и подключение

`POST /api/v1/token` (см. curl в английской части) возвращает JWT и `livekit_url`. Готового
веб-клиента в репозитории нет — демо будет жить на сайте-портфолио. Прямо сейчас подключиться проще
всего через [LiveKit Agents Playground](https://agents-playground.livekit.io): вставьте `livekit_url`
и `token`, подключитесь и скажите *«стол на четверых завтра в семь вечера»* (при `AGENT_LANGUAGE=ru`).
Мила поздоровается, вызовет `check_availability`, предложит ближайшие реальные слоты, повторит выбор
и вызовет `create_booking` после подтверждения — бронь появится в ops-core-api.

## Настройка речи (STT и TTS независимы)

Распознавание и синтез — два раздельных порта с раздельными блоками настроек. Поддерживается любая
комбинация: локальное распознавание с облачным голосом или наоборот.

- **STT:** `faster_whisper` (локально, по умолчанию) или `openai_compatible` (`STT_BASE_URL` /
  `STT_API_KEY` / `STT_MODEL`).
- **TTS:** `piper` (локально, по умолчанию) или `openai_compatible` (`TTS_BASE_URL` / `TTS_API_KEY`
  / `TTS_MODEL`).

Облачные клиенты не привязаны к провайдеру: конкретный сервис выбирает `*_BASE_URL` (OpenRouter,
OpenAI, свой сервер с OpenAI-совместимым audio-API), а не код. Провайдер **без** OpenAI-совместимого
контракта (например Cartesia) — это отдельная реализация того же порта `TTSClient` плюс одна ветка в
`llm/tts_factory.py`, по Open/Closed; в остальном коде не меняется ничего (см. английскую часть).

## Тесты

```bash
uv sync && uv run ruff check . && uv run mypy src
uv run pytest --cov=src/app/services --cov-report=term-missing
```

104 теста, ~94% покрытия слоя services. Ни базы, ни сети: порт `BookingRepository` заменяется
in-memory фейком (его вторая реализация), так что весь набор идёт офлайн. CI гоняет те же команды на
каждый push.
