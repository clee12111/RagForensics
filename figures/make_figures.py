"""
Generate README figures for Production RAG Stack Forensics.
Programmatic and reproducible, matching the polymarket-autopsy figure style.

Figures:
  fig1_architecture.png       — pipeline + MCP server data flow
  fig2_cost_vs_quality.png    — the headline cost finding (28x cost, flat faith)
  fig3_failure_origin.png     — failure-mode origin → fix mapping
"""

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np

# ── Shared style ──────────────────────────────────────────────────
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans"],
    "font.size": 11,
    "axes.edgecolor": "#d0d7de",
    "axes.linewidth": 1.0,
    "figure.facecolor": "white",
    "savefig.facecolor": "white",
})

INK = "#1f2328"
MUTE = "#57606a"
BLUE = "#1f6feb"
GREEN = "#1a7f37"
RED = "#cf222e"
AMBER = "#bf8700"
LIGHT = "#f6f8fa"
BORDER = "#d0d7de"


# ══════════════════════════════════════════════════════════════════
# FIG 1 — Architecture / data flow
# ══════════════════════════════════════════════════════════════════
def fig_architecture():
    fig, ax = plt.subplots(figsize=(10, 6.2))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    def box(x, y, w, h, label, sub="", fc=LIGHT, ec=BORDER, tc=INK, lw=1.4):
        b = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.6,rounding_size=2",
                           facecolor=fc, edgecolor=ec, linewidth=lw)
        ax.add_patch(b)
        ax.text(x + w / 2, y + h / 2 + (2.6 if sub else 0), label,
                ha="center", va="center", fontsize=10.5, fontweight="bold", color=tc)
        if sub:
            ax.text(x + w / 2, y + h / 2 - 3.4, sub, ha="center", va="center",
                    fontsize=8.2, color=MUTE)

    def arrow(x1, y1, x2, y2, color=MUTE, style="-|>"):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style,
                     mutation_scale=14, color=color, linewidth=1.6, shrinkA=2, shrinkB=2))

    # Title
    ax.text(50, 96, "Pipeline & Instrumentation", ha="center", fontsize=13.5,
            fontweight="bold", color=INK)

    # Query in
    box(38, 85, 24, 8, "User query", fc="#ddf4ff", ec=BLUE, tc=BLUE)
    arrow(50, 85, 50, 80)

    # Retrieve
    box(30, 68, 40, 11, "Hybrid Retrieval",
        "BM25 + dense  ·  RRF fusion  ·  Pinecone", fc=LIGHT)
    arrow(50, 68, 50, 63)

    # Generate
    box(30, 51, 40, 11, "Generation",
        "grounding prompt  ·  Sonnet / GPT / Gemini", fc=LIGHT)
    arrow(50, 51, 50, 46)

    # Answer
    box(38, 38, 24, 7, "Answer", fc="#dafbe1", ec=GREEN, tc=GREEN)

    # Langfuse tracing (right rail)
    box(76, 53, 20, 26, "Langfuse", "per-stage\ntracing", fc="#fff8c5", ec=AMBER, tc=AMBER)
    arrow(70, 73.5, 76, 70, color=AMBER)
    arrow(70, 56.5, 76, 60, color=AMBER)

    # Eval harness (left rail)
    box(4, 53, 20, 26, "Eval Harness", "150 Qs · 5 cats\nLLM judge", fc=LIGHT)
    arrow(24, 62, 30, 57, color=MUTE, style="-|>")

    # MCP server (bottom, spanning)
    box(20, 18, 60, 11, "Custom MCP Server",
        "6 tools  ·  stdio + Streamable HTTP", fc="#fbefff", ec="#8250df", tc="#8250df")
    # arrows from the three data sources into MCP
    arrow(14, 51, 30, 29, color="#8250df")     # eval -> mcp
    arrow(50, 38, 50, 29, color="#8250df")      # docs/answer -> mcp
    arrow(86, 51, 70, 29, color="#8250df")      # langfuse -> mcp
    ax.text(50, 13.5, "reads docs · eval results · live traces  →  agent-queryable",
            ha="center", fontsize=8.2, color=MUTE, style="italic")

    # Agent
    box(38, 3, 24, 7, "AI Agent", fc="#fbefff", ec="#8250df", tc="#8250df")
    arrow(50, 18, 50, 10, color="#8250df")

    plt.tight_layout()
    plt.savefig("/tmp/fig1_architecture.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("fig1_architecture.png")


# ══════════════════════════════════════════════════════════════════
# FIG 2 — Cost vs quality (the headline finding)
# ══════════════════════════════════════════════════════════════════
def fig_cost_vs_quality():
    providers = ["Gemini\n3.1 flash-lite", "Claude\nSonnet 4.6", "GPT-5.5"]
    tiers = ["(small)", "(mid)", "(flagship)"]
    faith = [4.62, 4.45, 4.39]
    cost = [0.18, 2.07, 5.06]
    colors = [GREEN, BLUE, "#8250df"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4.6))

    # Left: faithfulness (zoomed to show it's basically flat)
    bars1 = ax1.bar(range(3), faith, color=colors, width=0.62, edgecolor="white", linewidth=1.5)
    ax1.set_ylim(4.0, 4.8)
    ax1.set_ylabel("Faithfulness (0–5)", fontsize=10.5, color=INK)
    ax1.set_title("Quality: nearly identical", fontsize=11.5, fontweight="bold", color=INK, pad=10)
    ax1.set_xticks(range(3))
    ax1.set_xticklabels([f"{p}\n{t}" for p, t in zip(providers, tiers)], fontsize=8.6)
    for i, v in enumerate(faith):
        ax1.text(i, v + 0.012, f"{v:.2f}", ha="center", fontsize=10, fontweight="bold", color=INK)
    ax1.spines[["top", "right"]].set_visible(False)
    ax1.annotate("0.23 spread", xy=(1, 4.72), ha="center", fontsize=9,
                 color=MUTE, style="italic")

    # Right: cost (log-ish visual via linear but annotated)
    bars2 = ax2.bar(range(3), cost, color=colors, width=0.62, edgecolor="white", linewidth=1.5)
    ax2.set_ylim(0, 5.6)
    ax2.set_ylabel("Generation cost / 150 queries (USD)", fontsize=10.5, color=INK)
    ax2.set_title("Cost: 28× spread", fontsize=11.5, fontweight="bold", color=INK, pad=10)
    ax2.set_xticks(range(3))
    ax2.set_xticklabels([f"{p}\n{t}" for p, t in zip(providers, tiers)], fontsize=8.6)
    for i, v in enumerate(cost):
        ax2.text(i, v + 0.08, f"${v:.2f}", ha="center", fontsize=10, fontweight="bold", color=INK)
    ax2.spines[["top", "right"]].set_visible(False)

    fig.suptitle("Generation capability was not the bottleneck",
                 fontsize=13, fontweight="bold", color=INK, y=1.02)
    fig.text(0.5, -0.04,
             "Same retrieval, same judge, generator swapped. Tiers not matched — "
             "framed as realistic per-provider choices, not a ranking.",
             ha="center", fontsize=8.4, color=MUTE, style="italic")

    plt.tight_layout()
    plt.savefig("/tmp/fig2_cost_vs_quality.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("fig2_cost_vs_quality.png")


# ══════════════════════════════════════════════════════════════════
# FIG 3 — Failure-mode origin → fix
# ══════════════════════════════════════════════════════════════════
def fig_failure_origin():
    fig, ax = plt.subplots(figsize=(10, 4.8))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    ax.text(50, 95, "Where a failure originates determines what fixes it",
            ha="center", fontsize=13, fontweight="bold", color=INK)

    def box(x, y, w, h, label, sub="", fc=LIGHT, ec=BORDER, tc=INK):
        b = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.5,rounding_size=2",
                           facecolor=fc, edgecolor=ec, linewidth=1.4)
        ax.add_patch(b)
        ax.text(x + w / 2, y + h / 2 + (2.4 if sub else 0), label, ha="center", va="center",
                fontsize=9.6, fontweight="bold", color=tc)
        if sub:
            ax.text(x + w / 2, y + h / 2 - 3.0, sub, ha="center", va="center",
                    fontsize=7.6, color=MUTE)

    def arrow(x1, y1, x2, y2, color=MUTE):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                     mutation_scale=13, color=color, linewidth=1.5, shrinkA=2, shrinkB=2))

    # Three origin columns
    # Retrieval
    box(4, 60, 28, 12, "FM-1  Retrieval miss", "wrong chunks → fabrication",
        fc="#ddf4ff", ec=BLUE, tc=BLUE)
    box(4, 38, 28, 14, "Hybrid retrieval", "BM25 + dense (RRF)", fc=LIGHT, ec=BLUE)
    arrow(18, 60, 18, 52, color=BLUE)
    ax.text(18, 30, "FIXED", ha="center", fontsize=9, fontweight="bold", color=GREEN)
    ax.text(18, 25, "cross-ref 3.70→4.73", ha="center", fontsize=7.4, color=MUTE)

    # Generation
    box(36, 60, 28, 12, "FM-2 / FM-3", "leakage · contradiction",
        fc="#dafbe1", ec=GREEN, tc=GREEN)
    box(36, 38, 28, 14, "Grounding prompt", "few-shot, abstention", fc=LIGHT, ec=GREEN)
    arrow(50, 60, 50, 52, color=GREEN)
    ax.text(50, 30, "FIXED", ha="center", fontsize=9, fontweight="bold", color=GREEN)
    ax.text(50, 25, "faith=0: 5→0", ha="center", fontsize=7.4, color=MUTE)

    # Corpus
    box(68, 60, 28, 12, "FM-4  Synthesis gap", "no chunk has the join",
        fc="#ffebe9", ec=RED, tc=RED)
    box(68, 38, 28, 14, "Reranking (3 configs)", "LLM · cross-enc · diversity", fc=LIGHT, ec=RED)
    arrow(82, 60, 82, 52, color=RED)
    ax.text(82, 30, "NOT FIXED", ha="center", fontsize=9, fontweight="bold", color=RED)
    ax.text(82, 25, "neutral-to-negative", ha="center", fontsize=7.4, color=MUTE)

    # Origin labels
    ax.text(18, 78, "RETRIEVAL", ha="center", fontsize=8.5, fontweight="bold", color=BLUE)
    ax.text(50, 78, "GENERATION", ha="center", fontsize=8.5, fontweight="bold", color=GREEN)
    ax.text(82, 78, "CORPUS STRUCTURE", ha="center", fontsize=8.5, fontweight="bold", color=RED)

    ax.text(50, 12,
            "Reranking can only reorder chunks that exist — it cannot manufacture a join the corpus never wrote down.",
            ha="center", fontsize=8.4, color=MUTE, style="italic")

    plt.tight_layout()
    plt.savefig("/tmp/fig3_failure_origin.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("fig3_failure_origin.png")


if __name__ == "__main__":
    fig_architecture()
    fig_cost_vs_quality()
    fig_failure_origin()
    print("done")
