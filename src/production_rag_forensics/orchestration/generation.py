"""
Provider-agnostic generation layer for the RAG pipeline.

Supported providers:
    "anthropic" — claude-sonnet-4-6 with beta prompt-caching
                  $3.00/$15.00 per M in/out; $0.30/M cache read; $3.75/M cache write
    "openai"    — gpt-5.5 via standard chat completions
                  $5.00/$30.00 per M in/out; auto-caching at $2.50/M for cached tokens
    "google"    — gemini-3.1-pro-preview via google-genai SDK
                  $2.00/$12.00 per M in/out (≤200K context); no explicit caching

Public API:
    result = generate_answer(provider, system_prompt, user_content)

Returns GenerationResult with token counts, cost, provider, and model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# ── Models ────────────────────────────────────────────────────────────────────

_ANTHROPIC_MODEL = "claude-sonnet-4-6"
_OPENAI_MODEL    = "gpt-5.5"
_GOOGLE_MODEL    = "gemini-3.1-flash-lite"

# ── Pricing ($/million tokens) ────────────────────────────────────────────────

# (input_rate, output_rate, cache_read_rate, cache_write_rate)
_PRICING: dict[str, tuple[float, float, float, float]] = {
    "anthropic": (3.00, 15.00, 0.30, 3.75),
    "openai":    (5.00, 30.00, 2.50, 0.0),   # auto-cache billed at 50% input
    "google":    (0.25,  1.50, 0.0,  0.0),   # gemini-3.1-flash-lite; no caching
}

PROVIDERS = list(_PRICING)


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class GenerationResult:
    answer:                str
    input_tokens:          int
    output_tokens:         int
    cache_creation_tokens: int    # Anthropic only; 0 for others
    cache_read_tokens:     int    # Anthropic/OpenAI auto-cache; 0 for Google
    cost_usd:              float
    provider:              str    # "anthropic" | "openai" | "google"
    model:                 str    # exact model string used


# ── Cost helper ───────────────────────────────────────────────────────────────

def _compute_cost(
    provider: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int,
    cache_read_tokens: int,
) -> float:
    in_rate, out_rate, cr_rate, cw_rate = _PRICING[provider]
    M = 1_000_000

    if provider == "anthropic":
        # Anthropic SDK: input_tokens = new non-cached tokens only (cache_read is separate).
        # All three input categories are billed independently.
        return (
            input_tokens            * in_rate  / M
            + output_tokens         * out_rate / M
            + cache_read_tokens     * cr_rate  / M
            + cache_creation_tokens * cw_rate  / M
        )
    elif provider == "openai":
        # OpenAI: prompt_tokens includes cached tokens; cached portion billed at cr_rate.
        billed_input = input_tokens - cache_read_tokens
        return (
            billed_input        * in_rate  / M
            + cache_read_tokens * cr_rate  / M
            + output_tokens     * out_rate / M
        )
    else:  # google
        return (input_tokens * in_rate + output_tokens * out_rate) / M


# ── Provider implementations ──────────────────────────────────────────────────

def _generate_anthropic(system_prompt: str, user_content: str) -> GenerationResult:
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Wrap system prompt with cache_control so the stable prompt is cached.
    # The prompt must exceed 1024 tokens for ephemeral cache to activate;
    # the four worked examples push it well above that floor.
    system_block = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    response = client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=1024,
        temperature=0,
        system=system_block,
        messages=[{"role": "user", "content": user_content}],
    )

    answer        = response.content[0].text
    usage         = response.usage
    cache_created = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read    = getattr(usage, "cache_read_input_tokens", 0) or 0

    return GenerationResult(
        answer=answer,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_tokens=cache_created,
        cache_read_tokens=cache_read,
        cost_usd=_compute_cost(
            "anthropic",
            usage.input_tokens,
            usage.output_tokens,
            cache_created,
            cache_read,
        ),
        provider="anthropic",
        model=_ANTHROPIC_MODEL,
    )


def _generate_openai(system_prompt: str, user_content: str) -> GenerationResult:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    # GPT-5.5 does not support temperature != 1; omit it to use the default.
    response = client.chat.completions.create(
        model=_OPENAI_MODEL,
        max_completion_tokens=1024,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
    )

    answer       = response.choices[0].message.content
    usage        = response.usage
    input_tokens = usage.prompt_tokens
    out_tokens   = usage.completion_tokens

    # OpenAI auto-caching: cached_tokens lives in prompt_tokens_details
    cache_read = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cache_read = getattr(details, "cached_tokens", 0) or 0

    return GenerationResult(
        answer=answer,
        input_tokens=input_tokens,
        output_tokens=out_tokens,
        cache_creation_tokens=0,
        cache_read_tokens=cache_read,
        cost_usd=_compute_cost("openai", input_tokens, out_tokens, 0, cache_read),
        provider="openai",
        model=_OPENAI_MODEL,
    )


def _generate_google(system_prompt: str, user_content: str) -> GenerationResult:
    from google import genai        # type: ignore
    from google.genai import types  # type: ignore

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

    # gemini-3.1-flash-lite: thinking_budget=0 fully eliminates thinking tokens
    # (confirmed via smoke test: thoughts_token_count=None). max_output_tokens=1024
    # is now on the same footing as anthropic/openai — no thinking overhead.
    response = client.models.generate_content(
        model=_GOOGLE_MODEL,
        contents=user_content,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0,
            max_output_tokens=1024,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )

    answer = response.text
    usage  = response.usage_metadata
    inp    = usage.prompt_token_count
    out    = usage.candidates_token_count   # thinking=0, so this is the full answer

    return GenerationResult(
        answer=answer,
        input_tokens=inp,
        output_tokens=out,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        cost_usd=_compute_cost("google", inp, out, 0, 0),
        provider="google",
        model=_GOOGLE_MODEL,
    )


# ── Public entry point ────────────────────────────────────────────────────────

_DISPATCH = {
    "anthropic": _generate_anthropic,
    "openai":    _generate_openai,
    "google":    _generate_google,
}


def generate_answer(
    provider: str,
    system_prompt: str,
    user_content: str,
) -> GenerationResult:
    """
    Call the appropriate LLM and return a GenerationResult.

    Args:
        provider:      "anthropic" | "openai" | "google"
        system_prompt: plain text — provider implementations handle any
                       caching wrappers (e.g. cache_control for Anthropic)
        user_content:  formatted user message including query + context chunks

    Returns:
        GenerationResult with answer, token counts, cost, provider, model.
    """
    if provider not in _DISPATCH:
        raise ValueError(
            f"Unknown provider '{provider}'. Choose one of: {PROVIDERS}"
        )
    return _DISPATCH[provider](system_prompt, user_content)
