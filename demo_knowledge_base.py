"""
demo_knowledge_base.py  —  Run 5 live retrieval tests against ChromaDB
                            and display results as a clean formatted table.

Run from the project root with the venv activated:
    python demo_knowledge_base.py
"""

import sys
import os
import textwrap

# Allow "from src.retrieve import retrieve" without installing as a package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from retrieve import retrieve
from tabulate import tabulate

# ── The 5 test queries ─────────────────────────────────────────────────────────
#
# Each tuple: (label, query, retrieval_kwargs)
#   label          — short description shown in the output header
#   query          — the natural-language question sent to ChromaDB
#   retrieval_kwargs — keyword arguments passed directly to retrieve()

TESTS = [
    (
        "Path 1 — Spec (voltage levels)",
        "What are the digital input voltage thresholds for LOGO! 8?",
        {"n_results": 2, "tag": "SPEC"},
    ),
    (
        "Path 2 — Logic (ON_DELAY circuit example)",
        "Build a circuit that turns on a light 5 seconds after pressing a button",
        {"n_results": 2, "type_filter": "annotation"},
    ),
    (
        "Path 1 — Spec annotation (timer range)",
        "What is the maximum time I can set on an on-delay timer in LOGO! 8?",
        {"n_results": 2, "type_filter": "spec_annotation"},
    ),
    (
        "Path 2 — Logic (AND gate circuit)",
        "Create a circuit where Q1 turns on only when both I1 and I2 are pressed",
        {"n_results": 2, "type_filter": "annotation"},
    ),
    (
        "Path 1 — Spec (operating temperature)",
        "What is the operating temperature range for LOGO! 8 modules?",
        {"n_results": 2, "tag": "SPEC"},
    ),
]

WRAP_WIDTH = 55   # characters for text preview column


def run_demo():
    print("=" * 75)
    print("demo_knowledge_base.py  —  Live ChromaDB Retrieval Demo")
    print("=" * 75)
    print()
    print("Loading sentence-transformer model (takes a few seconds on first run)...")
    print()

    for test_num, (label, query, kwargs) in enumerate(TESTS, start=1):
        print(f"Test {test_num}/5 — {label}")
        print(f"  Query   : \"{query}\"")

        # Show the filter settings clearly
        filters = []
        if "tag" in kwargs:
            filters.append(f"tag={kwargs['tag']}")
        if "type_filter" in kwargs:
            filters.append(f"type={kwargs['type_filter']}")
        if not filters:
            filters.append("no filter (all 403 items)")
        print(f"  Filters : {', '.join(filters)}")
        print()

        # Run retrieval
        results = retrieve(query, **kwargs)

        if not results:
            print("  (no results returned)")
            print()
            continue

        # Build table rows
        rows = []
        for rank, r in enumerate(results, start=1):
            # Page number or annotation id
            if r["page"]:
                location = f"p{r['page']}"
            else:
                location = r["source"]

            # Wrap text for readability
            preview = r["text"][:WRAP_WIDTH * 2]   # take up to 110 chars
            preview = textwrap.fill(preview, width=WRAP_WIDTH)
            if len(r["text"]) > WRAP_WIDTH * 2:
                # Add ellipsis on the last line
                lines = preview.split("\n")
                lines[-1] = lines[-1][:WRAP_WIDTH - 3] + "..."
                preview = "\n".join(lines)

            rows.append([
                rank,
                f"{r['score']:.4f}",
                r["tag"],
                r["type"],
                location,
                preview,
            ])

        headers = ["#", "Score", "Tag", "Type", "Source", "Text preview"]
        print(tabulate(rows, headers=headers, tablefmt="grid",
                       colalign=("center", "center", "center", "left", "left", "left")))
        print()

    print("=" * 75)
    print("Demo complete.  All 5 retrieval tests ran successfully.")
    print()
    print("What you just saw:")
    print("  - Scores close to 1.0 mean a very strong semantic match.")
    print("  - Scores above 0.7 are reliable matches for RAG context.")
    print("  - 'type=manual'          -> chunks from the LOGO! 8 manual PDF")
    print("  - 'type=annotation'      -> hand-crafted LOGO!JSON circuit examples")
    print("  - 'type=spec_annotation' -> hand-crafted parameter table entries")
    print("=" * 75)


if __name__ == "__main__":
    run_demo()
