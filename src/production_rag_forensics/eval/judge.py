"""
Provider-agnostic LLM-as-judge for RAG faithfulness scoring.

Providers:
    "gemini"    — gemini-2.0-flash  ($0.10/$0.40 per M in/out tokens)
    "anthropic" — claude-sonnet-4-6 ($3.00/$15.00 per M in/out tokens)

Usage:
    judge = Judge(provider="gemini")
    result = judge.score(question, chunks, answer)
    print(result.faithfulness, result.reasoning)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

# ── Pricing ───────────────────────────────────────────────────────────────────

_PRICING: dict[str, tuple[float, float]] = {
    "gemini":    (0.10, 0.40),   # $/M input, $/M output
    "anthropic": (3.00, 15.00),
}

# ── Models ────────────────────────────────────────────────────────────────────

_GEMINI_MODEL    = "gemini-2.5-flash"
_ANTHROPIC_MODEL = "claude-sonnet-4-6"

# ── Prompts ───────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are an evaluation judge for a RAG system. Your job is to score "
    "whether an answer is faithful to the retrieved context. Faithful means "
    "every claim in the answer is directly supported by the provided chunks. "
    "Do not use outside knowledge."
)

_USER_TEMPLATE = """\
Question: {question}

Retrieved chunks:
{formatted_chunks}

Answer:
{answer}

Score the answer's faithfulness on this scale:
0 = hallucinated (contradicts or invents facts not in chunks)
1 = mostly wrong
2 = partially correct
3 = correct but incomplete
4 = correct and complete
5 = fully grounded, nothing added beyond the chunks

Respond in this exact JSON format:
{{"faithfulness": <int 0-5>, "reasoning": "<one sentence>"}}\
"""


def _format_chunks(chunks: list[dict]) -> str:
    lines = []
    for i, c in enumerate(chunks, start=1):
        lines.append(f"[{i}] {c.get('source_file', '')} (score={c.get('score', 0):.3f})")
        if c.get("text"):
            lines.append(c["text"])
        lines.append("")
    return "\n".join(lines).strip()


def _compute_cost(provider: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = _PRICING[provider]
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


def _parse_response(raw: str) -> tuple[int, str]:
    """
    Parse JSON from judge response.
    Raises ValueError with raw text on parse failure.
    """
    raw = raw.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(
            l for l in lines
            if not l.startswith("```")
        ).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Judge returned non-JSON response: {raw!r}") from exc

    faith = data.get("faithfulness")
    reasoning = data.get("reasoning", "")

    if not isinstance(faith, int) or not (0 <= faith <= 5):
        raise ValueError(f"Invalid faithfulness value {faith!r} in: {raw!r}")
    if not isinstance(reasoning, str):
        raise ValueError(f"Invalid reasoning value {reasoning!r} in: {raw!r}")

    return faith, reasoning


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class JudgeResult:
    faithfulness:  int
    reasoning:     str
    provider:      str
    input_tokens:  int
    output_tokens: int
    cost_usd:      float


# ── Judge ─────────────────────────────────────────────────────────────────────

class Judge:
    def __init__(self, provider: str = "gemini") -> None:
        if provider not in _PRICING:
            raise ValueError(f"Unknown provider '{provider}'. Choose: {list(_PRICING)}")
        self.provider = provider
        self._client  = None  # lazy init

    def _get_client(self):
        if self._client is not None:
            return self._client

        if self.provider == "gemini":
            from google import genai  # type: ignore
            self._client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        else:  # anthropic
            from anthropic import Anthropic
            self._client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

        return self._client

    def score(self, question: str, chunks: list[dict], answer: str) -> JudgeResult:
        formatted = _format_chunks(chunks)
        user_msg  = _USER_TEMPLATE.format(
            question=question,
            formatted_chunks=formatted,
            answer=answer,
        )

        if self.provider == "gemini":
            return self._score_gemini(user_msg)
        return self._score_anthropic(user_msg)

    def _score_gemini(self, user_msg: str) -> JudgeResult:
        from google import genai        # type: ignore
        from google.genai import types  # type: ignore

        client = self._get_client()
        response = client.models.generate_content(
            model=_GEMINI_MODEL,
            contents=user_msg,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                temperature=0,
                max_output_tokens=256,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )

        raw = response.text
        faith, reasoning = _parse_response(raw)

        usage = response.usage_metadata
        inp   = usage.prompt_token_count
        out   = usage.candidates_token_count

        return JudgeResult(
            faithfulness=faith,
            reasoning=reasoning,
            provider=_GEMINI_MODEL,
            input_tokens=inp,
            output_tokens=out,
            cost_usd=_compute_cost("gemini", inp, out),
        )

    def _score_anthropic(self, user_msg: str) -> JudgeResult:
        client = self._get_client()
        response = client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=150,
            temperature=0,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )

        raw   = response.content[0].text
        faith, reasoning = _parse_response(raw)

        inp = response.usage.input_tokens
        out = response.usage.output_tokens

        return JudgeResult(
            faithfulness=faith,
            reasoning=reasoning,
            provider=_ANTHROPIC_MODEL,
            input_tokens=inp,
            output_tokens=out,
            cost_usd=_compute_cost("anthropic", inp, out),
        )
