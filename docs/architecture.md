# Architecture

Layered, with a strict inward dependency rule — outer layers depend on inner ones, never the
reverse. This service follows the shared layout in `../architecture.md`, with **two delivery
layers instead of one**, because it runs as two processes.

```
┌───────────────────────────────────────────────────────────────┐
│        api/v1  (HTTP)              voice  (LiveKit worker)     │   ← delivery
├───────────────────────────────────────────────────────────────┤
│                       services  (business logic)              │
├───────────────────────────────────────────────────────────────┤
│        repositories          llm  (stt / tts / dialogue)      │
├───────────────────────────────────────────────────────────────┤
│                       interfaces  (ABC ports)                 │
├───────────────────────────────────────────────────────────────┤
│                    contracts  (frozen dataclasses)            │
├───────────────────────────────────────────────────────────────┤
│              core / exceptions / bootstrap                    │
└───────────────────────────────────────────────────────────────┘
```

## The two delivery layers

`api/v1/` and `voice/` are peers. Both are *outer* layers: both depend inward on `services/`,
and nothing in `services/` or below imports either one. A booking taken by voice runs the exact
same `ReservationService` a hypothetical HTTP booking would, which is why the service layer is
the one worth testing and the delivery layers stay thin.

- **`api/v1/`** — the HTTP process (`src/main.py`). Issues LiveKit join tokens and reports
  health. It never touches the booking domain.
- **`voice/`** — the LiveKit agent worker (`src/entrypoint.py`). Runs the STT → LLM → TTS
  pipeline and exposes the booking tools to the model. Its `@function_tool` wrappers delegate
  straight into `services/dialog/`, adding nothing of their own.

## What is deliberately absent

No `models/` and no `db/`: this service owns no database. Its only data source is ops-core-api,
reached over HTTP through `repositories/core_api_booking.py`. That repository implements the same
`BookingRepository` port a SQL repository would, so the services above it cannot tell the
transport apart — the Adapter pattern doing exactly what it is for.

## Layer responsibilities

| Layer | Holds | Rule |
|---|---|---|
| `contracts/` | Frozen `slots=True` dataclasses passed between layers | Never imports schemas, api, or services |
| `schemas/` | Pydantic models at the HTTP boundary | Converted to contracts in the router; never passed down |
| `interfaces/` | ABC ports (`BookingRepository`, `STTClient`, `TTSClient`, `SlotRankingStrategy`) | Services depend on these, not on concrete classes |
| `repositories/` | The HTTP adapter to ops-core-api | Returns contracts, raises typed exceptions, lets no `httpx` type escape |
| `llm/` | Factories + concrete STT/TTS/LLM clients | The only place a provider client is constructed |
| `services/` | Business logic: reservations, dialogue tools, tokens | No LiveKit, no FastAPI, no HTTP types |
| `api/v1/`, `voice/` | Delivery | Validate/adapt input, call a service, adapt output |
| `core/` | Settings, logging, request-id, the retry decorator | No business logic |
| `exceptions/` | Typed hierarchy under `BaseAppException` | Carries `status_code` / `error_code` |
| `bootstrap/` | `ApplicationContainer`, manual DI via `cached_property` | Lazy singletons; each process builds only what it touches |

## Patterns, and where to read them

| Pattern | Where | Why it is there |
|---|---|---|
| **Adapter** | `repositories/core_api_booking.py` | Makes an HTTP service satisfy a database-shaped port. |
| **Adapter** | `llm/faster_whisper_stt_client.py`, `llm/piper_tts_client.py` | Wrap a local model / local binary behind livekit's `stt.STT` / `tts.TTS`. |
| **Factory** | `llm/factory.py`, `llm/stt_factory.py`, `llm/tts_factory.py` | The one place each provider client is built; provider chosen by settings, never by calling code. |
| **Strategy** | `interfaces/booking/slot_ranking_strategy.py` + `services/booking/ranking.py` | How free slots are ranked against a requested time; a second rule adds a class, not an `if`. |
| **Template Method** | `services/dialog/flow.py` | Fixes the order of the system prompt's sections; a subclass overrides a step but cannot reshuffle the skeleton. |
