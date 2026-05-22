"""
Provider-agnostic LLM-as-judge for RAG faithfulness and retrieval precision scoring.

Providers:
    "gemini"    — gemini-2.5-flash  ($0.10/$0.40 per M in/out tokens)
    "anthropic" — claude-sonnet-4-6 ($3.00/$15.00 per M in/out tokens)

Usage:
    judge = Judge(provider="gemini")
    result = judge.score(question, chunks, answer)
    print(result.faithfulness, result.precision_at_5, result.relevant_chunks)
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
    "Do not use outside knowledge. "
    "You also assess retrieval quality: for each numbered chunk, judge whether "
    "it is relevant to answering the question."
)

_USER_TEMPLATE = """\
Question: {question}

Retrieved chunks:
{formatted_chunks}

Answer:
{answer}

Provide two assessments:

1. FAITHFULNESS (0-5): is the answer grounded in the chunks?
0 = hallucinated (contradicts or invents facts not in chunks)
1 = mostly wrong
2 = partially correct
3 = correct but incomplete
4 = correct and complete
5 = fully grounded, nothing added beyond the chunks

2. CHUNK RELEVANCE: for each chunk 1-5, is it relevant to the question?
A chunk is relevant if it contains information that helps answer the question,
irrelevant if it is off-topic or unrelated. Judge relevance to the QUESTION,
not whether the answer used it.

Respond in this exact JSON format:
{{"faithfulness": <int 0-5>, "faithfulness_reason": "<one phrase>", "relevant_chunks": [<list of relevant chunk numbers, e.g. 1,2,4>], "relevance_reason": "<one phrase>"}}\
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


def _parse_response(raw: str) -> tuple[int, str, list[int], str]:
    """
    Parse JSON from judge response.
    Returns (faithfulness, faithfulness_reason, relevant_chunks, relevance_reason).
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
    reasoning = data.get("faithfulness_reason", data.get("reasoning", ""))
    relevant_chunks = data.get("relevant_chunks", [])
    relevance_reason = data.get("relevance_reason", "")

    if not isinstance(faith, int) or not (0 <= faith <= 5):
        raise ValueError(f"Invalid faithfulness value {faith!r} in: {raw!r}")
    if not isinstance(reasoning, str):
        raise ValueError(f"Invalid faithfulness_reason value {reasoning!r} in: {raw!r}")
    if not isinstance(relevant_chunks, list):
        raise ValueError(f"Invalid relevant_chunks value {relevant_chunks!r} in: {raw!r}")

    # Coerce chunk numbers to int, filter invalid
    relevant_chunks = [int(x) for x in relevant_chunks if isinstance(x, (int, float))]

    return faith, reasoning, relevant_chunks, relevance_reason


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class JudgeResult:
    faithfulness:    int
    reasoning:       str          # faithfulness reason
    relevant_chunks: list[int]    # e.g. [1, 2, 4]
    precision_at_5:  float        # len(relevant_chunks) / 5
    relevance_reason: str
    provider:        str
    input_tokens:    int
    output_tokens:   int
    cost_usd:        float


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
                max_output_tokens=512,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )

        raw = response.text
        faith, reasoning, relevant_chunks, relevance_reason = _parse_response(raw)

        usage = response.usage_metadata
        inp   = usage.prompt_token_count
        out   = usage.candidates_token_count

        return JudgeResult(
            faithfulness=faith,
            reasoning=reasoning,
            relevant_chunks=relevant_chunks,
            precision_at_5=len(relevant_chunks) / 5,
            relevance_reason=relevance_reason,
            provider=_GEMINI_MODEL,
            input_tokens=inp,
            output_tokens=out,
            cost_usd=_compute_cost("gemini", inp, out),
        )

    def _score_anthropic(self, user_msg: str) -> JudgeResult:
        client = self._get_client()
        response = client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=512,
            temperature=0,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )

        raw   = response.content[0].text
        faith, reasoning, relevant_chunks, relevance_reason = _parse_response(raw)

        inp = response.usage.input_tokens
        out = response.usage.output_tokens

        return JudgeResult(
            faithfulness=faith,
            reasoning=reasoning,
            relevant_chunks=relevant_chunks,
            precision_at_5=len(relevant_chunks) / 5,
            relevance_reason=relevance_reason,
            provider=_ANTHROPIC_MODEL,
            input_tokens=inp,
            output_tokens=out,
            cost_usd=_compute_cost("anthropic", inp, out),
        )
