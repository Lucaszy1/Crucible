"""Single client wrapper for Meta Model API calls.

Every model call in the project goes through this module, so retries with
backoff, on-disk response caching, per-exercise spend logging, and the hard
daily spend guard all live in one place.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".llm_cache"
SPEND_FILE = ROOT / "runs" / "spend.jsonl"


def _load_env_file(path) -> bool:
    """Load one dotenv file without overriding existing variables."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    load_dotenv(path, override=False)
    return True


_load_env_file(ROOT / ".env")

BASE_URL = os.environ.get("MODEL_API_BASE", "https://api.meta.ai/v1")

# Role names map to model IDs from the build spec. Pass a full model ID
# directly to skip routing (handy for tryouts and tests).
ROLE_MODELS = {
    "red": "muse-spark-1.3",
    "blue": "muse-spark-1.3",
    "blue-open": "meta-models/muse-spark-open-1026",
    "auditor": "muse-wonder-1.0",
    "referee": "muse-wonder-1.0",
    "dev": "muse-spark-1.3-contributor",
}

# Conservative cost estimates in USD per 1K tokens: (input, output).
# These are placeholders until measured billing lands. They feed the spend
# guard only, never any user-facing invoice.
PRICE_PER_1K_USD = {
    "muse-spark-1.3": (0.002, 0.006),
    "meta-models/muse-spark-open-1026": (0.001, 0.003),
    "muse-wonder-1.0": (0.005, 0.015),
    "muse-spark-1.3-contributor": (0.001, 0.002),
}
DEFAULT_PRICE_PER_1K_USD = (0.002, 0.006)

DEFAULT_DAILY_LIMIT_USD = 5.0
DEFAULT_REASONING_EFFORT = "low"
MAX_RETRIES = 4

# Per-role sampling temperature. Red varies most so episodes differ;
# blue stays steadier. LLM_TEMPERATURE overrides every role.
ROLE_TEMPERATURES = {
    "red": 0.8,
    "blue": 0.5,
}
DEFAULT_TEMPERATURE = 0.7

# Exception names that are worth another attempt after a short wait.
_RETRYABLE_NAMES = {
    "RateLimitError",
    "APIConnectionError",
    "APITimeoutError",
    "Timeout",
    "InternalServerError",
    "ServiceUnavailableError",
}
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class SpendLimitError(RuntimeError):
    """Raised when a model call would pass the daily spend cap."""


def model_for(role_or_model: str) -> str:
    """Resolve a role name to its model ID.

    Known roles come from the build spec table. A value containing a slash
    passes through untouched so open-weights IDs keep working. Anything else
    unknown raises ValueError instead of guessing.
    """
    if role_or_model in ROLE_MODELS:
        return ROLE_MODELS[role_or_model]
    if role_or_model in set(ROLE_MODELS.values()) or "/" in role_or_model:
        return role_or_model
    raise ValueError(f"unknown model role: {role_or_model!r}")


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate one call's cost in USD from the placeholder price table."""
    per_in, per_out = PRICE_PER_1K_USD.get(model, DEFAULT_PRICE_PER_1K_USD)
    return (input_tokens / 1000.0) * per_in + (output_tokens / 1000.0) * per_out


def reasoning_effort_default() -> str:
    """Read the reasoning effort override from the environment."""
    value = (os.environ.get("LLM_REASONING_EFFORT", "") or "").strip()
    return value or DEFAULT_REASONING_EFFORT


def daily_limit_usd() -> float:
    """Read the daily spend cap from the environment."""
    try:
        return float(os.environ.get("LLM_DAILY_LIMIT_USD", DEFAULT_DAILY_LIMIT_USD))
    except ValueError:
        return DEFAULT_DAILY_LIMIT_USD


def temperature_for(role: str) -> float:
    """Resolve sampling temperature for one role.

    LLM_TEMPERATURE wins when it parses as a float. Otherwise red and
    blue use their defaults so episodes vary; every other role falls
    back to the shared default.
    """
    override = (os.environ.get("LLM_TEMPERATURE", "") or "").strip()
    if override:
        try:
            return float(override)
        except ValueError:
            pass
    return ROLE_TEMPERATURES.get(role, DEFAULT_TEMPERATURE)


def spent_today(today: str | None = None) -> float:
    """Sum recorded spend for one day (default: current local day)."""
    day = today or date.today().isoformat()
    if not SPEND_FILE.exists():
        return 0.0
    total = 0.0
    for line in SPEND_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("day") == day:
            try:
                total += float(entry.get("cost_usd", 0.0))
            except (TypeError, ValueError):
                continue
    return total


def record_spend(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    exercise_id: str | None = None,
    cached: bool = False,
) -> dict:
    """Append one spend entry to the jsonl log and return it."""
    cost = 0.0 if cached else estimate_cost(model, input_tokens, output_tokens)
    entry = {
        "day": date.today().isoformat(),
        "ts": time.time(),
        "model": model,
        "exercise_id": exercise_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 6),
        "cached": cached,
    }
    SPEND_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SPEND_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    return entry


def check_spend_guard(extra_estimate: float = 0.0) -> float:
    """Block when recorded spend for today already hit the cap.

    Returns the current total when the call may proceed. Raises
    SpendLimitError otherwise. No model traffic happens past the cap.
    """
    limit = daily_limit_usd()
    total = spent_today()
    if total + extra_estimate >= limit:
        raise SpendLimitError(
            f"daily model spend cap reached: ${total:.2f} of ${limit:.2f} used"
        )
    return total


def _cache_key(
    model: str,
    messages: list,
    tools: list | None,
    extra: dict,
    exercise_id: str | None = None,
    seed: str | None = None,
) -> str:
    entry = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "extra": extra,
        "exercise_id": exercise_id,
    }
    if seed is not None:
        entry["seed"] = seed
    blob = json.dumps(entry, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _read_cache(key: str) -> dict | None:
    path = CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(key: str, payload: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{key}.json"
    path.write_text(json.dumps(payload, default=str), encoding="utf-8")


def _cacheable(*, finish_reason, content: str, tool_calls: list, tools) -> bool:
    """Decide whether a reply is worth serving again from disk.

    Replies cut off by the token limit, replies with no text, and
    replies missing a tool call when tools were offered are never
    cached, so one degenerate response cannot repeat for every turn.
    """
    if finish_reason == "length":
        return False
    if not str(content or "").strip():
        return False
    if tools and not tool_calls:
        return False
    return True


def _retryable(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in _RETRYABLE_NAMES:
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status in _RETRYABLE_STATUS:
        return True
    http_status = getattr(getattr(exc, "response", None), "status_code", None)
    return isinstance(http_status, int) and http_status in _RETRYABLE_STATUS


def _default_client():
    """Build an OpenAI-compatible client for the Meta Model API."""
    _load_env_file(ROOT / ".env")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "the openai package is missing; run pip install -r requirements.txt"
        ) from exc
    api_key = os.environ.get("MODEL_API_KEY")
    if not api_key:
        raise RuntimeError("MODEL_API_KEY is not set; copy .env.example to .env")
    return OpenAI(base_url=BASE_URL, api_key=api_key)


def _message_to_dict(message) -> tuple[str, list]:
    content = getattr(message, "content", None) or ""
    calls = []
    for call in getattr(message, "tool_calls", None) or []:
        func = getattr(call, "function", SimpleNamespace(name="", arguments="{}"))
        calls.append(
            {
                "id": getattr(call, "id", ""),
                "name": getattr(func, "name", ""),
                "arguments": getattr(func, "arguments", "{}"),
            }
        )
    return content, calls


def chat(
    role: str,
    messages: list,
    *,
    tools: list | None = None,
    exercise_id: str | None = None,
    client=None,
    use_cache: bool = True,
    max_retries: int = MAX_RETRIES,
    temperature: float | None = None,
    max_tokens: int = 1536,
    reasoning_effort: str | None = None,
    seed: str | None = None,
) -> dict:
    """Run one chat completion with guard, cache, and retries.

    Returns a plain dict with content, tool_calls, usage, model, and a
    cached flag. Tests may pass a fake client; production passes none and
    gets a real Meta Model API client. The cache key includes the
    exercise id, plus the episode seed when one is given, so paired
    arms never share entries. The seed only splits cache keys; it
    does not match play between arms. A None temperature resolves
    per role.
    """
    model = model_for(role)
    effort = (reasoning_effort or "").strip() or reasoning_effort_default()
    extra = {
        "temperature": temperature if temperature is not None else temperature_for(role),
        "max_tokens": max_tokens,
        "reasoning_effort": effort,
    }
    key = _cache_key(model, messages, tools, extra, exercise_id, seed)

    if use_cache:
        hit = _read_cache(key)
        if hit is not None:
            usage = hit.get("usage", {})
            record_spend(
                model=model,
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
                exercise_id=exercise_id,
                cached=True,
            )
            return {**hit, "cached": True}

    check_spend_guard()
    owned = client is None
    client = client if client is not None else _default_client()

    kwargs: dict = {"model": model, "messages": messages, **extra}
    if tools:
        kwargs["tools"] = tools

    last_exc: BaseException | None = None
    try:
        for attempt in range(max(1, max_retries)):
            try:
                response = client.chat.completions.create(**kwargs)
                break
            except Exception as exc:  # noqa: BLE001 - sorted below by retryable
                last_exc = exc
                if not _retryable(exc) or attempt + 1 >= max(1, max_retries):
                    raise
                delay = (2**attempt) + random.uniform(0, 0.5)
                time.sleep(delay)
        else:  # pragma: no cover - loop always breaks or raises
            raise last_exc  # type: ignore[misc]
    finally:
        # A default client holds a connection pool; callers that pass
        # their own client (tests, sharing) keep owning it.
        if owned:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    choice = response.choices[0]
    message = choice.message
    content, tool_calls = _message_to_dict(message)
    finish_reason = getattr(choice, "finish_reason", None)
    usage_obj = getattr(response, "usage", None)
    input_tokens = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage_obj, "completion_tokens", 0) or 0)

    record_spend(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        exercise_id=exercise_id,
    )
    payload = {
        "content": content,
        "tool_calls": tool_calls,
        "finish_reason": finish_reason,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "model": model,
        "cached": False,
    }
    if use_cache and _cacheable(
        finish_reason=finish_reason,
        content=content,
        tool_calls=tool_calls,
        tools=tools,
    ):
        _write_cache(key, payload)
    return payload
