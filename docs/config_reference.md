# Partnership_AI — config.json Reference

This document describes every setting in `config.json`, what it controls,
and what happens when you change it.

> **When are changes picked up?** Most settings are read at startup via
> `app_config.get_config()` (cached on first call). Changes take effect on
> the next program restart. A few settings (notably `agent.max_cost_usd_per_turn`)
> are re-read live during execution, so they apply mid-session.

---

## `logging`

Controls the root Python logger that all modules write to.

```json
"logging": {
    "level": "INFO",
    "to_file": true,
    "file_path": "cr_logs/app.log"
}
```

| Key | Type | Default | Effect |
|---|---|---|---|
| `level` | string | `"INFO"` | Root log level. `"DEBUG"` enables httpx/openai HTTP request logs (shows every Groq API call's URL + status code). `"INFO"` suppresses them for cleaner output. `"WARNING"` silences most operational logs. `"ERROR"` shows only errors. |
| `to_file` | bool | `true` | If `true`, logs are also written to `file_path`. If `false`, logs only go to console/stderr. |
| `file_path` | string | `"cr_logs/app.log"` | File path for the log output. Relative paths resolve from the project root. The directory is created if it doesn't exist. |

**Changing `level` to `"DEBUG"`:** You'll see every HTTP request made to the
Groq API (`POST https://api.groq.com/openai/v1/chat/completions "HTTP/1.1 200 OK"`),
plus all `DEBUG`-level log messages from internal modules. Useful for
diagnosing API issues or tracing execution flow.

**Changing `level` to `"WARNING"`:** Startup messages, ethics pass results,
plugin loading, and scheduler status will be suppressed. Only warnings and
errors appear.

---

## `ethics`

Controls the two-pass ethics validation system.

```json
"ethics": {
    "enabled": true,
    "threshold": "lenient",
    "allow_warnings": true,
    "blocked_placeholder": "[⚠️] The generated answer was blocked by ethics checks."
}
```

| Key | Type | Default | Effect |
|---|---|---|---|
| `enabled` | bool | `true` | If `false`, the ethics reflector is not created and no ethics checks run. All outbound responses skip Pass 1 (keyword) and Pass 2 (LLM) validation. Responses are sent unfiltered. |
| `threshold` | string | `"lenient"` | Reserved for future strictness levels (`"lenient"`, `"moderate"`, `"strict"`). Currently the ethics system uses a two-pass approach that doesn't branch on this value — it's read into config but all three thresholds run the same checks. Future versions may gate which principles are mandatory vs advisory based on this. |
| `allow_warnings` | bool | `true` | If `true`, responses that trigger ethics *warnings* (but not hard blocks) are still sent to the user. If `false`, warnings are treated as blocks. |
| `blocked_placeholder` | string | `"[⚠️] The generated answer was blocked by ethics checks."` | The text shown to the user when a response is blocked by ethics validation. You can customize this to any string. |

**Disabling ethics (`"enabled": false`):** No ethics checks run at all. The
`ethics_reflector` attribute on `AdaptiveAgent` is set to `None`, and the
`_ethics_check` / `_ethics_deep_review` methods return immediately without
blocking. MemoryEngine's lazy `ethics` property also won't be used for
insight storage validation.

---

## `session`

Controls session dump behavior on exit.

```json
"session": {
    "dump_on_exit": true,
    "dump_dir": "cr_logs/session_dumps"
}
```

| Key | Type | Default | Effect |
|---|---|---|---|
| `dump_on_exit` | bool | `true` | If `true`, the entire session's turn log is written to a plaintext file in `dump_dir` when the program exits. If `false`, no session dump is created. |
| `dump_dir` | string | `"cr_logs/session_dumps"` | Directory for session dump files. Created if it doesn't exist. Relative paths resolve from the project root. |

**Session dump file format:** `session-<user_hash[:8]>-<ISO timestamp>.txt`

**Disabling (`"dump_on_exit": false`):** No session history is saved. If you
crash or exit unexpectedly, the conversation is lost.

---

## `learning`

Controls the adaptive learning system that extracts patterns from
conversations.

```json
"learning": {
    "enabled": true,
    "max_batch_size": 500,
    "min_interval_minutes": 60
}
```

| Key | Type | Default | Effect |
|---|---|---|---|
| `enabled` | bool | `true` | If `false`, the learning system does not extract or store patterns from conversations. Existing learned data is preserved but no new data is collected. |
| `max_batch_size` | int | `500` | Maximum number of learned items stored in a single batch. Prevents unbounded memory growth. When this limit is reached, oldest items are evicted first. |
| `min_interval_minutes` | int | `60` | Minimum time (in minutes) between learning cycles. Prevents the system from reprocessing too frequently and consuming excess LLM calls. |

**Setting `min_interval_minutes` to `0`:** Learning runs on every turn,
consuming more LLM calls and increasing latency.

**Increasing `max_batch_size`:** More historical patterns are retained, but
memory usage grows and LLM summarization calls take longer.

---

## `fallback`

Controls what happens when the primary LLM backend is unavailable.

```json
"fallback": {
    "use_groq": true,
    "use_stub": false
}
```

| Key | Type | Default | Effect |
|---|---|---|---|
| `use_groq` | bool | `true` | If `true`, the system attempts to use the Groq API as the LLM backend. If `false`, Groq is never called even if the API key is set. |
| `use_stub` | bool | `false` | If `true`, uses a local stub responder instead of any real LLM. Useful for offline testing. The stub returns canned responses without making network calls. |

**`use_groq: false` + `use_stub: false`:** No LLM backend is available. The
system degrades gracefully — LLM-dependent features (summarization, ethics
Pass 2, self-reflection, planning) fall back to heuristic or empty responses.

**`use_stub: true`:** All LLM calls return a stub response. No network calls
are made. Useful for testing the full agent pipeline without API costs.

---

## `llm_logging`

Controls whether LLM interactions are logged to a file with full prompts
and responses.

```json
"llm_logging": {
    "enabled": true,
    "log_path": "plans.log",
    "rotate_mb": 5,
    "rotate_keep": 3
}
```

| Key | Type | Default | Effect |
|---|---|---|---|
| `enabled` | bool | `true` | If `true`, every LLM call's full prompt, response, model, and status are written to `log_path`. If `false`, no LLM interaction logging occurs. |
| `log_path` | string | `"plans.log"` | File path for LLM interaction logs. Relative to project root. |
| `rotate_mb` | int | `5` | When the log file exceeds this size in MB, it's rotated (renamed to `.1`, `.2`, etc.). |
| `rotate_keep` | int | `3` | Maximum number of rotated backup files to keep. Oldest are deleted. |

**Disabling (`"enabled": false`):** No LLM prompts or responses are logged
to disk. This saves disk space but makes it impossible to audit what the
LLM was asked or how it responded after the fact.

**Each log entry contains:** timestamp, model name, attempt number,
success/failure status, the full prompt text, and the full response text.

**Rotation example with `rotate_mb: 5, rotate_keep: 3`:**
```
plans.log        ← current (under 5MB)
plans.log.1      ← most recent rotation
plans.log.2      ← older
plans.log.3      ← oldest (deleted when .4 would be created)
```

---

## `sandbox`

Controls the `SandboxedExecutor` that runs generated code during CR
review and self-testing.

```json
"sandbox": {
    "timeout_seconds": 30,
    "max_memory_mb": 256,
    "max_cpu_seconds": 10,
    "use_firejail_if_available": true
}
```

| Key | Type | Default | Effect |
|---|---|---|---|
| `timeout_seconds` | int | `30` | Maximum wall-clock seconds a sandboxed process can run before it's killed. |
| `max_memory_mb` | int | `256` | Maximum memory (MB) a sandboxed process can allocate. Processes exceeding this are terminated. |
| `max_cpu_seconds` | int | `10` | Maximum CPU seconds a sandboxed process can consume. |
| `use_firejail_if_available` | bool | `true` | If `true` and `firejail` is installed on the system, sandboxed code runs inside a firejail container for additional isolation. If firejail isn't installed, falls back to standard subprocess execution. |

**Decreasing `timeout_seconds`:** Faster CR review but complex code may be
killed before it finishes, producing false test failures.

**Increasing `max_memory_mb`:** Allows sandboxed code to handle larger
datasets, but risks OOM conditions on constrained systems (like Termux on
mobile).

**Disabling firejail (`"use_firejail_if_available": false`):** Sandboxed
code runs in a standard subprocess with only the timeout/memory/CPU limits.
Less isolation but no dependency on firejail being installed.

---

## `reviewer`

Controls the Change Request (CR) review pipeline.

```json
"reviewer": {
    "smoke_test_timeout_seconds": 30,
    "run_full_test_suite_on_accept": true,
    "backup_history_limit": 20
}
```

| Key | Type | Default | Effect |
|---|---|---|---|
| `smoke_test_timeout_seconds` | int | `30` | Timeout for smoke tests run against proposed CR changes. If a test suite exceeds this, the test is considered failed. |
| `run_full_test_suite_on_accept` | bool | `true` | If `true`, the full test suite (`tests/run_tests.py`) is run against a scratch copy of the project with the proposed CR change overlaid before the change is accepted. If `false`, only `ast.parse()` syntax validation is performed — no tests run. |
| `backup_history_limit` | int | `20` | Maximum number of backup files kept in `cr_logs/backups/`. When a new backup is created beyond this limit, the oldest is deleted. |

**Disabling `run_full_test_suite_on_accept`:** CRs are accepted after only
syntax validation. Faster, but a change that compiles fine can break other
modules' contracts without being caught.

**Increasing `backup_history_limit`:** More rollback points are preserved,
giving you more history to revert to. Disk usage increases.

---

## `agent`

Controls the agent's per-turn execution budget and cost tracking.

```json
"agent": {
    "max_plan_steps": 12,
    "max_runtime_seconds": 60,
    "max_llm_calls_per_turn": 8,
    "max_replan_attempts": 1,
    "trace_rotate_mb": 5,
    "trace_rotate_keep": 3,
    "max_cost_usd_per_turn": 0.01,
    "cost_per_million_input_tokens_usd": 0.15,
    "cost_per_million_output_tokens_usd": 0.60,
    "_cost_pricing_note": "..."
}
```

| Key | Type | Default | Effect |
|---|---|---|---|
| `max_plan_steps` | int | `12` | Maximum number of action steps the agent can execute in a single turn. When this limit is reached, the agent stops executing and returns what it has. |
| `max_runtime_seconds` | int | `60` | Maximum wall-clock seconds a single turn can run. When exceeded, the agent aborts the current plan and returns a partial response. |
| `max_llm_calls_per_turn` | int | `8` | Maximum number of LLM API calls allowed in a single turn. Prevents runaway token consumption. When exhausted, further LLM calls are skipped and the agent degrades gracefully. |
| `max_replan_attempts` | int | `1` | How many times the agent can re-plan when a step fails (schema validation error, action failure, etc.). After this many replans, the agent gives up on the turn. |
| `trace_rotate_mb` | int | `5` | Agent trace files (per-turn execution logs) are rotated when they exceed this size in MB. |
| `trace_rotate_keep` | int | `3` | Maximum number of rotated trace files to keep. |
| `max_cost_usd_per_turn` | float | `0.01` | Dollar cost cap per turn. When the estimated cost of LLM calls in a turn reaches this, further LLM calls are blocked. Re-read live during execution — changes apply mid-session. |
| `cost_per_million_input_tokens_usd` | float | `0.15` | Price per million input tokens, used to estimate turn cost. Used in `TurnTrace.estimated_cost_usd()`. |
| `cost_per_million_output_tokens_usd` | float | `0.60` | Price per million output tokens, used to estimate turn cost. Used in `TurnTrace.estimated_cost_usd()`. |
| `_cost_pricing_note` | string | — | Documentation-only field. Not read by code. Explains the pricing source. |

**Increasing `max_plan_steps`:** The agent can complete more complex
multi-step tasks in a single turn, but each turn takes longer and consumes
more LLM calls.

**Decreasing `max_llm_calls_per_turn`:** Cheaper per-turn cost, but the
agent may not be able to complete complex reasoning (planning + ethics +
self-reflection + summarization can easily need 4-6 calls).

**Changing cost rates:** If you switch to a different Groq model, update
`cost_per_million_input_tokens_usd` and `cost_per_million_output_tokens_usd`
to match the model's actual pricing (check https://groq.com/pricing). The
cost enforcement is only as accurate as these rates.

**Setting `max_cost_usd_per_turn` to `0` or `null`:** No cost-based
limiting — only the call-count limit (`max_llm_calls_per_turn`) applies.

---

## Fallback Behavior

If `config.json` is missing, corrupted, or missing a key, the system falls
back to built-in defaults defined in `app_config.py` (`_DEFAULTS` dict).
A warning is printed to the console: `⚠ app_config: could not load
config.json (...); using built-in defaults`.

The defaults mirror the shipped `config.json` values, so a missing file
produces identical behavior to the default config — just without
customizations.

---

## Quick Reference: Common Changes

| Goal | Setting | Value |
|---|---|---|
| See HTTP request logs | `logging.level` | `"DEBUG"` |
| Minimal console output | `logging.level` | `"WARNING"` |
| Disable all ethics checks | `ethics.enabled` | `false` |
| Don't save session dumps | `session.dump_on_exit` | `false` |
| Faster CR review (no tests) | `reviewer.run_full_test_suite_on_accept` | `false` |
| Allow more steps per turn | `agent.max_plan_steps` | `20` (or higher) |
| Allow more LLM calls per turn | `agent.max_llm_calls_per_turn` | `12` (or higher) |
| Disable LLM logging to disk | `llm_logging.enabled` | `false` |
| Offline testing (no API calls) | `fallback.use_stub` | `true` |
| Allow more memory for sandboxed code | `sandbox.max_memory_mb` | `512` |
