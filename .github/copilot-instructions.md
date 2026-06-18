# Copilot Instructions — Observability Library

Observability Library is a **publishable Python library** (not a web service)
for shipping structured logs to **Loki/Grafana** and traces to an **OTLP/HTTP**
backend (Tempo, Grafana Cloud, etc.), with the two correlated for click-through
navigation in Grafana. It builds on the stdlib `logging` module: an async
`LokiHandler` (**aiohttp**), a sync `SyncLokiHandler` (**requests** + queue +
worker thread), **pydantic** label schemas, and an **optional** OpenTelemetry
tracer setup behind the `[tracing]` extra. Tooling: **uv** package manager,
**ruff** linting, **pytest / pytest-asyncio**.

When reviewing pull requests, focus on **public API integrity**, **consistency
with the existing code**, and **sound design / SOLID principles**. Use the
guidance below.

## 1. Public API integrity

Because this is a versioned, importable library, the public surface must stay
stable and safe:

- Anything exported from `observability_library/__init__.py` (`__all__`) is the
  **public contract**. Adding symbols is fine; renaming or removing
  `LokiHandler`, `SyncLokiHandler`, `LokiLoggerLabels`, `TraceContextFilter`,
  `TracingConfigurationError`, `reset_tracing`, or `setup_tracer_provider`, or
  changing their signatures in a breaking way, requires a deliberate reason and
  a version bump (`__version__` in `__init__.py` and `version` in
  `pyproject.toml` must move together).
- **Handler/schema signatures are part of the contract.** Don't change the
  `__init__` parameters of `LokiHandler` / `SyncLokiHandler` (`url`, `labels`,
  `timeout`, `auth_token`, plus `queue_size` on the sync handler) or the
  `LokiLoggerLabels` fields without justification. New parameters must be
  keyword-optional with backward-compatible defaults.
- **Async and sync handlers stay in parity.** A change to one handler's
  behaviour (constructor surface, label handling, record forwarding, failure
  semantics) should be mirrored in the other unless the difference is inherently
  transport-specific (the docstrings call out that they "differ only in
  transport"). Flag drift between `handler.py` and `sync_handler.py`.
- **Optional dependencies stay optional.** OpenTelemetry lives behind the
  `[tracing]` extra. Imports of `opentelemetry.*` must remain **guarded** —
  inside functions/`TYPE_CHECKING` (as in `tracing.py`) or wrapped in
  `try/except ImportError` (as in `log_correlation.py`). Never add a top-level
  `import opentelemetry` to a module on the default import path, and never add
  opentelemetry to `[project.dependencies]`.
- **No hard crash when tracing extras are absent.** `TraceContextFilter.filter`
  must stay a no-op when OTel isn't installed (returns `True`), and
  `setup_tracer_provider` must raise the typed `TracingConfigurationError` with
  the install hint, not a raw `ImportError`.
- **Secure defaults are preserved.** Keep `require_tls=True` and
  `require_auth=True` as defaults in `setup_tracer_provider`; do not weaken the
  `https://` and non-empty `Authorization` checks. The `auth_token` Bearer
  header on both handlers must remain. Do not log credentials — failure logging
  goes through `log_send_failure`, which deliberately records only the exception
  **class** (the URL may embed credentials).

## 2. Consistency with existing code (same patterns)

New code must match the established conventions — flag deviations:

- **Typed schemas:** label/record shapes belong in pydantic models like
  `LokiLoggerLabels` (validators, `Field(...)` constraints, `to_dict()` /
  `to_loki_labels()` helpers that drop `None`). Don't introduce ad-hoc dicts
  where a schema is the established pattern.
- **Record forwarding:** both handlers ship *every* non-standard `LogRecord`
  attribute as a JSON field via `build_loki_payload`. Application fields flow in
  through `logger.info(..., extra={...})` or a `logging.Filter` — not through
  new handler parameters. The standard-attribute filter list
  (`_STANDARD_RECORD_ATTRS`) and the underscore-prefix skip are the single
  source of truth for what gets excluded.
- **Log ↔ trace correlation:** `TraceContextFilter` stamps `trace_id`,
  `span_id`, and `trace_flags` (formatted `032x` / `016x` / `02x`) onto records.
  Preserve these exact field names and formats — Grafana derived fields depend
  on them.
- **Handler parity & naming:** keep the parallel structure between
  `LokiHandler` and `SyncLokiHandler` (same `emit` → `build_loki_payload` →
  transport flow, same `_build_headers` / header construction, same
  `log_send_failure(kind, exc)` diagnostics with `"async"` / `"sync"` kinds).
- **README and `example.py` track the API.** Any change to the public surface
  (new symbol, new parameter, changed default, new security check) must be
  reflected in `README.md` (the component table, security-defaults table, BSP
  tuning table) and, where relevant, in `example.py`. Flag PRs that change
  behaviour without updating these.

## 3. Design patterns & SOLID principles

Review for maintainable, well-structured code:

- **Single Responsibility:** each module stays narrow — `_payload.py` builds
  payloads and reports failures, `tracing.py` only configures a provider (it
  intentionally does **not** call `*Instrumentor().instrument()`; that's the
  application's job), `log_correlation.py` only bridges trace context onto
  records, `log_schema.py` only validates labels. Flag logic that crosses these
  boundaries.
- **DRY:** payload construction and send-failure diagnostics are shared in
  `_payload.py` precisely because the two handlers are otherwise identical.
  Flag copy-pasted payload/header/serialization logic that should live in
  `_payload.py` instead of being duplicated divergently across the handlers.
- **Dependency Inversion / don't hardcode transport:** `build_loki_payload`
  takes the already-formatted message as a parameter so it has no `Handler`
  dependency; the handler owns formatter selection. Keep transport (aiohttp vs.
  requests vs. queue) out of shared helpers, and keep auto-instrumentation
  choices out of the library.
- **Open/Closed:** prefer adding keyword-only parameters with sane defaults
  (as `setup_tracer_provider` does for the BSP knobs, and the handlers do for
  `timeout` / `queue_size`) over editing call sites or branching on type.
- **Thread-safety / async-safety:** the async `LokiHandler.emit` must stay
  **non-blocking** — it schedules `_async_send` on the running loop and must not
  make synchronous network/disk/CPU calls in `emit` or `_async_send`. The sync
  handler's `emit` must stay non-blocking via the bounded queue + daemon
  worker, dropping on `queue.Full` rather than blocking the caller. Preserve the
  `_provider_lock` guarding the cached provider in `tracing.py`. Flag any
  blocking I/O introduced into the async path or any shared mutable state
  accessed without a lock.
- **Robust failure handling:** `emit` must never raise into the caller's logging
  call — keep the broad `except` + `handleError(record)` / `log_send_failure`
  pattern, the `_safe_json_dumps` fallback, and the worker thread that survives
  send errors. Don't narrow these `except` clauses in ways that let a bad record
  or a dead backend crash the application or kill the worker thread.

## 4. What NOT to flag

- Code style, import ordering, and formatting — enforced by **ruff**. Do not
  comment on them.
- Intentionally broad `except Exception` / `except BaseException` blocks in the
  handlers, `_safe_json_dumps`, and the worker drain loop: they are documented
  and deliberate (a malformed record or unreachable Loki must not break the
  app or stop shipping logs).
- Test scaffolding under `tests/` and the use of `reset_tracing()` for test
  isolation.
- Pre-existing patterns that the PR merely follows (review the diff, not the
  repo's legacy choices).

Keep findings focused and actionable. Prioritize correctness, security, and
backward compatibility of the public API over stylistic preferences.
