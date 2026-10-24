"""One LLM wrapper (OpenAI-compatible API) for Groq, Gemini or OpenAI.

The LLM is used only to understand intent (router) and to phrase answers. It never
decides eligibility, access or numbers. If no key is set or a call fails, callers
fall back to deterministic code, so the app always runs (offline mode).
"""

import json
import os
import re
import time

from app import config

PROVIDERS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key": "GROQ_API_KEY",
             "model": "llama-3.3-70b-versatile"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "key": "GEMINI_API_KEY",
               "model": "gemini-2.0-flash"},
    "openai": {"base_url": None, "key": "OPENAI_API_KEY", "model": "gpt-4o-mini"},
}


class LLMUnavailable(RuntimeError):
    pass


def provider():
    p = config.LLM_PROVIDER
    if p in PROVIDERS and os.getenv(PROVIDERS[p]["key"]):
        return p
    return None


def model_name():
    p = provider()
    return (config.LLM_MODEL or PROVIDERS[p]["model"]) if p else "offline-rules"


def available():
    return provider() is not None


def chat(system, user, json_mode=False, max_tokens=600, stats=None):
    """Single chat completion. Raises LLMUnavailable if no provider/key or the call fails."""
    p = provider()
    if p is None:
        raise LLMUnavailable("No LLM configured (set LLM_PROVIDER and its API key).")
    from openai import OpenAI

    cfg = PROVIDERS[p]
    client = OpenAI(api_key=os.getenv(cfg["key"]), base_url=cfg["base_url"], timeout=config.LLM_TIMEOUT)
    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
    started = time.perf_counter()
    try:
        resp = client.chat.completions.create(
            model=model_name(), temperature=0, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], **kwargs)
    except Exception as exc:  # network, rate limit, auth
        raise LLMUnavailable(f"LLM call failed: {exc}") from exc
    if stats is not None:
        stats["llm_calls"] = stats.get("llm_calls", 0) + 1
        stats["llm_ms"] = stats.get("llm_ms", 0) + int((time.perf_counter() - started) * 1000)
        usage = getattr(resp, "usage", None)
        if usage:
            stats["tokens"] = stats.get("tokens", 0) + (usage.total_tokens or 0)
    return (resp.choices[0].message.content or "").strip()


def chat_json(system, user, stats=None):
    """chat() that returns a dict; one retry on invalid JSON."""
    last = None
    for _ in range(2):
        raw = chat(system, user, json_mode=True, stats=stats)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        try:
            return json.loads(match.group(0) if match else raw)
        except (json.JSONDecodeError, AttributeError) as exc:
            last = exc
    raise LLMUnavailable(f"LLM returned invalid JSON twice: {last}")
