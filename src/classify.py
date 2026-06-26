"""
classify.py  —  Intent classifier for the LOGO! PLC RAG assistant.

Given a user's natural language query, returns one of two labels:

    "spec"   →  Path 1: retrieve manual chunks, return a text explanation
    "logic"  →  Path 2: retrieve circuit examples, generate LOGO!JSON

Strategy: keyword patterns handle the obvious cases first (fast, free).
If the query is ambiguous, a tiny GPT-4o-mini call resolves it.
"""

import re
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()


# ── Keyword patterns ───────────────────────────────────────────────────────────
#
# \b means "word boundary" — so "build" matches "build" but NOT "building".
# re.search() checks anywhere in the string, not just the start.

# Priority logic patterns — these override spec patterns when both sides match.
# They are so specifically about circuit construction that question words
# like "what does" or "how do I" do not make them ambiguous.
# Example: "What does it look like to wire up an OR gate?" is still a
# circuit-generation request even though it starts with "what does".
PRIORITY_LOGIC_PATTERNS = [
    r"\bwire\s+up\b",                          # "wire up two inputs"
    r"\bwire\s+a\b",                           # "wire a timer to an output"
]

# Words that strongly signal the user wants a circuit GENERATED.
LOGIC_PATTERNS = [
    r"\bcreate\b",                             # "create a circuit"
    r"\bbuild\b",                              # "build me a timer"
    r"\bdesign\b",                             # "design a start-stop circuit"
    r"\bgenerate\b",                           # "generate a LOGO! program"
    r"\bimplement\b",                          # "implement a counter"
    r"\bdraw\s+a\b",                           # "draw a ladder diagram"
    r"\bmake\s+a\b",                           # "make a circuit that..."
    r"\bwrite\s+a\s+(?:circuit|program|ladder)\b",  # "write a circuit for..."
    r"\bshow\s+me\s+a\s+circuit\b",            # "show me a circuit that..."
    r"\bin\s+a\s+circuit\b",                   # "how the AND gate is used in a circuit"
    r"\bladder\s+diagram\b",                   # "ladder diagram for..."
    r"\bfbd\b",                                # "FBD for an AND gate"
    r"\bfunction\s+block\s+diagram\b",         # spelled out
]

# Words that strongly signal the user wants INFORMATION or an EXPLANATION.
SPEC_PATTERNS = [
    r"\bwhat\s+is\b",                          # "what is the AND gate?"
    r"\bwhat\s+are\b",                         # "what are the voltage levels?"
    r"\bwhat\s+does\b",                        # "what does the latch block do?"
    r"\bwhat'?s\b",                            # "what's the difference..."
    r"\bhow\s+does\b",                         # "how does the retentive timer work?"
    r"\bhow\s+many\b",                         # "how many inputs does LOGO! have?"
    r"\bhow\s+much\b",                         # "how much current can an output handle?"
    r"\bexplain\b",                            # "explain the off-delay timer"
    r"\bdescribe\b",                           # "describe how NAND works"
    r"\btell\s+me\s+about\b",                 # "tell me about LOGO! outputs"
    r"\bdifference\s+between\b",               # "difference between on-delay and off-delay"
    r"\bwhen\s+should\s+i\b",                 # "when should I use a latch?"
    r"\bwhen\s+would\s+i\b",                  # "when would I use XOR?"
    r"\bspecification\b",                      # "what is the voltage specification?"
    r"\bparameter\b",                          # "what are the timer parameters?"
    r"\bvoltage\b",                            # "input voltage range"
    r"\btemperature\b",                        # "operating temperature"
    r"\bmaximum\b",                            # "maximum counter value"
    r"\bminimum\b",                            # "minimum input voltage"
]


# ── Step 1: keyword classification ────────────────────────────────────────────

def _keyword_classify(query_lower: str):
    """
    Return "logic" or "spec" if keyword patterns clearly indicate one category.
    Return None if the query is genuinely ambiguous — caller will use the LLM.

    Three-stage check:
      1. Priority logic patterns  — win regardless of spec matches.
      2. Normal logic vs spec     — return whichever side matched, if unambiguous.
      3. Both or neither matched  — return None so the LLM resolves it.
    """
    # Stage 1: priority patterns override everything
    if any(re.search(p, query_lower) for p in PRIORITY_LOGIC_PATTERNS):
        return "logic"

    # Stage 2: normal keyword check
    logic_hit = any(re.search(p, query_lower) for p in LOGIC_PATTERNS)
    spec_hit  = any(re.search(p, query_lower) for p in SPEC_PATTERNS)

    if logic_hit and not spec_hit:
        return "logic"   # clear generate/build request
    if spec_hit and not logic_hit:
        return "spec"    # clear question/explanation request

    # Stage 3: ambiguous — both or neither matched
    return None


# ── Step 2: LLM fallback for ambiguous queries ────────────────────────────────

def _llm_classify(query: str) -> str:
    """
    Ask GPT-4o-mini to classify the query when keywords are not conclusive.

    The prompt is tiny (system + one query) so this costs a fraction of a cent.
    temperature=0 makes the answer deterministic and reproducible.
    """
    system_prompt = (
        "You classify questions about Siemens LOGO! 8 PLC programming.\n"
        "Reply with exactly one word — either 'spec' or 'logic'. Nothing else.\n\n"
        "'logic' = the user wants to CREATE, BUILD, or GENERATE a circuit, "
        "ladder diagram, or logic program.\n"
        "'spec'  = the user wants to UNDERSTAND or KNOW something — "
        "explanations, specifications, voltage ranges, parameters, or how things work."
    )

    try:
        client = OpenAI()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            max_tokens=5,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": query},
            ],
        )
        result = response.choices[0].message.content.strip().lower()
        return "logic" if "logic" in result else "spec"

    except Exception as e:
        # If the API is unavailable, default to "spec".
        # Spec is the safer fallback — it returns a text explanation
        # rather than attempting circuit generation that might go wrong.
        print(f"  [classify] LLM call failed ({e}). Defaulting to 'spec'.")
        return "spec"


# ── Public interface ───────────────────────────────────────────────────────────

def classify(query: str) -> str:
    """
    Classify a user query as 'spec' or 'logic'.

    Parameters
    ----------
    query : str
        The user's natural language question or request.

    Returns
    -------
    str
        'spec'   → send to Path 1 (manual retrieval + explanation)
        'logic'  → send to Path 2 (example retrieval + LOGO!JSON generation)
    """
    query_lower = query.lower().strip()

    # Try keyword matching first — no API call needed
    result = _keyword_classify(query_lower)
    if result is not None:
        return result

    # Keywords were ambiguous — use LLM to decide
    return _llm_classify(query)


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    # Ground truth test cases:  (query, expected_label)
    test_cases = [
        # --- clear LOGIC queries ---
        ("Create a circuit that turns on Q1 when I1 and I2 are both pressed", "logic"),
        ("Build me a timer that delays the output by 5 seconds",              "logic"),
        ("Design a motor start-stop circuit using a latch",                   "logic"),
        ("Make a ladder diagram for stairway lighting",                       "logic"),
        ("Generate a circuit that counts 10 items on a conveyor belt",        "logic"),
        ("Write a circuit for an off-delay timer on output Q1",               "logic"),
        ("I need an FBD that turns on Q1 when I1 OR I2 is active",           "logic"),

        # --- clear SPEC queries ---
        ("What is the maximum on-delay timer value in LOGO! 8?",             "spec"),
        ("How does the retentive timer work?",                                "spec"),
        ("What are the digital input voltage levels?",                        "spec"),
        ("Explain the difference between ON_DELAY and OFF_DELAY",            "spec"),
        ("How many digital outputs does LOGO! 8 have?",                      "spec"),
        ("What is the operating temperature range for LOGO! 8?",             "spec"),
        ("Describe how the latch block works",                                "spec"),
        ("What is the difference between AND gate and NAND gate?",           "spec"),

        # --- ambiguous queries (will use LLM) ---
        ("How do I create a circuit for a safety interlock?",                "logic"),
        ("Can you show me how the AND gate is used in a circuit?",           "logic"),
        ("What does it look like to wire up an OR gate?",                    "logic"),
    ]

    print("=" * 65)
    print("classify.py  —  Self-test")
    print("=" * 65)
    print(f"{'Query':<53} {'Got':<7} {'Expected':<8} {'OK?'}")
    print("-" * 65)

    passed = 0
    failed = 0

    for query, expected in test_cases:
        result = classify(query)
        ok = "PASS" if result == expected else "FAIL"
        if result == expected:
            passed += 1
        else:
            failed += 1
        short_query = query[:50] + "..." if len(query) > 50 else query
        print(f"{short_query:<53} {result:<7} {expected:<8} {ok}")

    print("-" * 65)
    print(f"Result: {passed}/{len(test_cases)} passed, {failed} failed.")
    print()
    if failed == 0:
        print("All tests passed. classify.py is ready.")
    else:
        print("Some tests failed — review the keyword patterns above.")
