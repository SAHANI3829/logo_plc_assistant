"""
pipeline.py  —  Main RAG query handler for the LOGO! PLC assistant.

This module is the brain of the system. It connects every other piece:
  1. classify.py  — decides which of the two paths to take
  2. retrieve.py  — fetches relevant content from ChromaDB
  3. OpenAI API   — generates the final answer

TWO PATHS
─────────
Path 1  "spec"   — User asked a question or wants an explanation.
   classify → "spec"
   retrieve → top 3 chunks from the full knowledge base
   GPT-4o-mini → reads the chunks and writes a clear text answer
   return → answer text + list of source citations

Path 2  "logic"  — User wants a circuit built / generated.
   classify → "logic"
   retrieve → top 2 matching circuit examples (type=annotation)
   GPT-4o-mini → uses the examples as a guide, generates LOGO!JSON
   validate → basic structure check; retry once if invalid
   return → LOGO!JSON dict + brief description

PUBLIC INTERFACE
────────────────
    from src.pipeline import run

    result = run("What is the operating temperature of LOGO! 8?")
    result = run("Build a circuit that turns on Q1 after 5 seconds")

    result["intent"]          # "spec" or "logic"
    result["answer"]          # text answer (Path 1) or circuit description (Path 2)
    result["logojson"]        # dict  (Path 2 only, None on Path 1)
    result["sources"]         # list of retrieval metadata dicts
    result["elapsed_seconds"] # float — useful for evaluation
    result["error"]           # None if all went well; error message string if something failed
"""

import json
import os
import sys
import time

# Windows terminals default to the cp1252 codepage, which cannot print the
# ─ character used in this file's self-test output. Force UTF-8 so those
# prints don't crash before any API call even happens.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from openai import OpenAI
from dotenv import load_dotenv

# Load the OPENAI_API_KEY from the .env file in the project root.
# load_dotenv() looks for .env starting from the current working directory
# and walking up. Running from C:\logo_plc_assistant finds it immediately.
load_dotenv()

# Add src/ to the path so we can import our own modules cleanly.
# This is needed when pipeline.py is run directly (python src/pipeline.py).
# When imported by app.py it is already handled by sys.path.
sys.path.insert(0, os.path.dirname(__file__))

from classify import classify
from retrieve import retrieve
from validate import validate


# ── Configuration ──────────────────────────────────────────────────────────────

MODEL       = "gpt-4o-mini"
TEMPERATURE = 0     # deterministic output — important for reproducible evaluation
MAX_TOKENS_SPEC  = 600   # enough for a thorough beginner-friendly explanation
MAX_TOKENS_LOGIC = 800   # JSON blocks can be verbose; give the LLM room to breathe

SPEC_N_RESULTS  = 3   # number of chunks fed to the spec LLM prompt
LOGIC_N_RESULTS = 2   # number of example circuits fed to the logic LLM prompt

MAX_LOGIC_RETRIES = 2   # how many times to retry if the LLM returns bad JSON


# ── System prompts ─────────────────────────────────────────────────────────────
#
# These are the "instructions" sent to GPT-4o-mini before the user message.
# They are carefully worded to constrain the model's behaviour.

SPEC_SYSTEM = """\
You are a friendly and precise assistant for Siemens LOGO! 8 PLC programming.
Your job is to answer questions using ONLY the documentation excerpts provided.

Rules you must follow:
1. Base your answer entirely on the provided excerpts — do not use outside knowledge.
2. If the answer is clearly present, give a clear, beginner-friendly explanation.
3. Cite manual page numbers where available, for example: (Manual p.45).
4. If the answer cannot be found in the excerpts, say exactly this sentence:
   "I could not find this information in the LOGO! 8 manual excerpts I was given."
5. Do not invent, guess, or extrapolate information beyond what is in the excerpts.\
"""

LOGIC_SYSTEM = """\
You are an expert at generating LOGO!JSON circuits for Siemens LOGO! 8 PLCs.
LOGO!JSON is a compact JSON format that fully describes a PLC logic circuit.

CLOSED VOCABULARY — you may ONLY use these 18 block types (exact case):
  AND  OR  NOT  NAND  NOR  XOR
  ON_DELAY  OFF_DELAY  ON_OFF_DELAY  RETENTIVE_TIMER
  UP_COUNTER  DOWN_COUNTER
  LATCH  PULSE
  RISING_EDGE  FALLING_EDGE
  COMPARATOR
  OUTPUT

STRICT RULES — follow every one or the circuit will be rejected:
  1. Physical input pins: I1, I2, I3, I4, I5, I6, I7, I8 only.
  2. Physical output pins: Q1, Q2, Q3, Q4 only — used in OUTPUT blocks.
  3. Block IDs: B1, B2, B3 ... sequential integers only.
  4. Every circuit MUST end with at least one OUTPUT block that has a "pin" field.
  5. Every "input" or "inputs" value must be a valid pin (I1-I8) or a valid block ID.
  6. No block may reference a block ID that has not yet been defined above it.
  7. Timer blocks (ON_DELAY etc.) take a single "input" (string) and a "T" value (e.g. "5s").
  8. Gate blocks (AND, OR etc.) take "inputs" (a list of strings).
  9. NOT, LATCH, RISING_EDGE, FALLING_EDGE take a single "input" (string).
 10. OUTPUT takes a single "input" (string) and a "pin" (e.g. "Q1").

OUTPUT FORMAT:
  Return ONLY the raw JSON object. No markdown. No code fences. No explanation.

CORRECT FORMAT EXAMPLE:
{
  "description": "one sentence describing what the circuit does",
  "blocks": [
    {"id": "B1", "type": "AND", "inputs": ["I1", "I2"]},
    {"id": "B2", "type": "ON_DELAY", "input": "B1", "T": "5s"},
    {"id": "B3", "type": "OUTPUT", "input": "B2", "pin": "Q1"}
  ]
}\
"""


# ── Prompt builders ────────────────────────────────────────────────────────────

def _build_spec_user_message(query: str, chunks: list) -> str:
    """
    Assemble the user message for Path 1.

    Structure:
        DOCUMENTATION EXCERPTS:

        [Excerpt 1] (Manual p.XX) or (spec_annotation)
        <text of chunk>

        [Excerpt 2] ...

        ---
        QUESTION: <user query>
    """
    parts = []
    for i, chunk in enumerate(chunks, start=1):
        # Build a human-readable source label
        if chunk.get("page"):
            label = f"Manual p.{chunk['page']}"
        else:
            label = chunk.get("source", "knowledge base")

        parts.append(f"[Excerpt {i}]  ({label})\n{chunk['text']}")

    context = "\n\n".join(parts)

    return (
        f"DOCUMENTATION EXCERPTS:\n\n"
        f"{context}\n\n"
        f"{'─' * 60}\n\n"
        f"QUESTION: {query}"
    )


def _build_logic_user_message(query: str, examples: list) -> str:
    """
    Assemble the user message for Path 2.

    Each example becomes a labelled reference block showing:
      - the original request text
      - the LOGO!JSON that satisfies it

    The LLM uses these as few-shot demonstrations to understand
    the expected format before generating the new circuit.
    """
    example_parts = []
    for i, ex in enumerate(examples, start=1):
        # The query field holds the original annotation request text
        request_text = ex.get("query", ex["text"][:120])

        # logojson is stored as a JSON string in ChromaDB — parse it back
        raw_lj = ex.get("logojson")
        if raw_lj:
            try:
                lj_obj = json.loads(raw_lj) if isinstance(raw_lj, str) else raw_lj
                lj_pretty = json.dumps(lj_obj, indent=2)
            except (json.JSONDecodeError, TypeError):
                lj_pretty = str(raw_lj)
        else:
            lj_pretty = "{}"

        example_parts.append(
            f"REFERENCE EXAMPLE {i}:\n"
            f"Request: {request_text}\n"
            f"LOGO!JSON:\n{lj_pretty}"
        )

    examples_text = "\n\n".join(example_parts)

    return (
        f"{examples_text}\n\n"
        f"{'─' * 60}\n\n"
        f"Now generate a LOGO!JSON circuit for this new request:\n"
        f"{query}"
    )


# ── LLM callers ───────────────────────────────────────────────────────────────

def _call_spec_llm(query: str, chunks: list) -> str:
    """
    Call GPT-4o-mini for Path 1 (spec/explain).
    Returns the model's answer as a plain string.
    """
    client   = OpenAI()   # reads OPENAI_API_KEY from the environment automatically
    user_msg = _build_spec_user_message(query, chunks)

    response = client.chat.completions.create(
        model=MODEL,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS_SPEC,
        messages=[
            {"role": "system", "content": SPEC_SYSTEM},
            {"role": "user",   "content": user_msg},
        ],
    )
    return response.choices[0].message.content.strip()


def _call_logic_llm(query: str, examples: list, error_hint: str = None) -> str:
    """
    Call GPT-4o-mini for Path 2 (logic/generate).

    error_hint is used on retry calls — it tells the model what was wrong
    with the previous response so it can fix it.

    Returns the raw LLM output string (should be JSON).
    """
    client   = OpenAI()
    user_msg = _build_logic_user_message(query, examples)

    # On a retry, append the error to the user message so the model can self-correct.
    if error_hint:
        user_msg += (
            f"\n\n{'─' * 60}\n\n"
            f"IMPORTANT — your previous response was rejected for this reason:\n"
            f"{error_hint}\n\n"
            f"Please fix the issue and return only the corrected JSON object."
        )

    response = client.chat.completions.create(
        model=MODEL,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS_LOGIC,
        messages=[
            {"role": "system", "content": LOGIC_SYSTEM},
            {"role": "user",   "content": user_msg},
        ],
    )
    return response.choices[0].message.content.strip()


# ── JSON parser and basic validator ───────────────────────────────────────────

def _parse_logojson(raw_text: str):
    """
    Parse the LLM output as JSON.

    The model sometimes wraps its response in markdown code fences
    (```json ... ```) even when told not to. This function strips them
    before parsing.

    Returns:
        (dict, None)          on success
        (None, error_message) on failure
    """
    text = raw_text.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove the opening fence line (```json or ```)
        lines = lines[1:]
        # Remove the closing fence line if present
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
        return data, None
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"


# ── Main entry point ──────────────────────────────────────────────────────────

def run(query: str) -> dict:
    """
    Process a user's natural language query through the full RAG pipeline.

    Parameters
    ----------
    query : str
        The user's question or circuit request.

    Returns
    -------
    dict with these keys (always present):
        intent          "spec" or "logic"
        query           the original query string
        answer          text answer (Path 1) or circuit description (Path 2)
        logojson        LOGO!JSON dict (Path 2 only; None on Path 1)
        sources         list of retrieval metadata dicts
        elapsed_seconds float — query processing time in seconds
        error           None if successful; error message string if something went wrong
        retries         int — number of LLM retries needed (Path 2 only; 0 means first try worked)
    """
    start_time = time.time()

    # ── Step 1: Classify the intent ───────────────────────────────────────────
    intent = classify(query)

    # ── Step 2a: Path 1 — Spec / Explain ──────────────────────────────────────
    if intent == "spec":

        # Retrieve the most relevant chunks from the full knowledge base.
        # We do not filter by tag here — the semantic similarity search finds the
        # right content regardless of whether it is tagged SPEC, GATE, LD, or ST.
        chunks = retrieve(query, n_results=SPEC_N_RESULTS)

        try:
            answer = _call_spec_llm(query, chunks)
            error  = None
        except Exception as exc:
            answer = "Sorry, I could not generate an answer due to an API error."
            error  = str(exc)

        return {
            "intent":          "spec",
            "query":           query,
            "answer":          answer,
            "logojson":        None,
            "sources": [
                {
                    "source": c["source"],
                    "page":   c.get("page"),
                    "tag":    c["tag"],
                    "type":   c["type"],
                    "score":  c["score"],
                }
                for c in chunks
            ],
            "elapsed_seconds": round(time.time() - start_time, 2),
            "error":           error,
            "retries":         0,
        }

    # ── Step 2b: Path 2 — Logic / Generate ────────────────────────────────────
    else:

        # Retrieve the two most similar hand-crafted circuit examples.
        # These act as few-shot demonstrations — they show the LLM the exact
        # format it should use for the new circuit.
        examples = retrieve(query, n_results=LOGIC_N_RESULTS, type_filter="annotation")

        logojson   = None
        error      = None
        error_hint = None
        retries    = 0

        for attempt in range(1, MAX_LOGIC_RETRIES + 1):

            try:
                raw_output = _call_logic_llm(query, examples, error_hint)
            except Exception as exc:
                error = f"API error on attempt {attempt}: {exc}"
                break

            # Try to parse the output as JSON
            logojson, parse_error = _parse_logojson(raw_output)

            if parse_error:
                retries    += 1
                error_hint  = parse_error
                logojson    = None
                if attempt == MAX_LOGIC_RETRIES:
                    error = f"Could not parse valid JSON after {MAX_LOGIC_RETRIES} attempts. Last error: {parse_error}"
                continue

            # Check full 10-rule structure (validate.py)
            valid, validation_errors = validate(logojson)
            if not valid:
                retries    += 1
                error_hint  = "\n".join(f"- {e}" for e in validation_errors)
                logojson    = None
                if attempt == MAX_LOGIC_RETRIES:
                    error = f"LOGO!JSON failed validation after {MAX_LOGIC_RETRIES} attempts. Last errors:\n{error_hint}"
                continue

            # All checks passed — break out of the retry loop
            break

        # The description field from the generated JSON is our "answer" text.
        # It is a human-readable one-sentence summary of the circuit.
        answer = logojson.get("description", "") if logojson else ""

        return {
            "intent":          "logic",
            "query":           query,
            "answer":          answer,
            "logojson":        logojson,
            "sources": [
                {
                    "source": ex["source"],
                    "tag":    ex["tag"],
                    "score":  ex["score"],
                }
                for ex in examples
            ],
            "elapsed_seconds": round(time.time() - start_time, 2),
            "error":           error,
            "retries":         retries,
        }


# ── Self-test ──────────────────────────────────────────────────────────────────
#
# Run this to verify the full pipeline end-to-end:
#   python src/pipeline.py

if __name__ == "__main__":

    import os

    print("=" * 70)
    print("pipeline.py  —  End-to-end self-test")
    print("=" * 70)
    print()

    # Check that the API key is available before running
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("ERROR: OPENAI_API_KEY is not set.")
        print()
        print("You need to create a .env file in the project root.")
        print("It should contain one line:")
        print()
        print("    OPENAI_API_KEY=sk-...")
        print()
        print("Then run this script again.")
        sys.exit(1)

    # Mask the key in output — show only first 8 characters
    masked_key = api_key[:8] + "..." + api_key[-4:]
    print(f"API key found: {masked_key}")
    print()

    # ── Test 1: Path 1 — Spec query ───────────────────────────────────────────
    q1 = "What is the operating temperature range for LOGO! 8 modules?"
    print(f"{'─' * 70}")
    print(f"TEST 1 — Path 1  (spec)")
    print(f"Query:  \"{q1}\"")
    print(f"{'─' * 70}")

    r1 = run(q1)

    print(f"Intent:   {r1['intent']}")
    print(f"Time:     {r1['elapsed_seconds']} s")
    print(f"Error:    {r1['error']}")
    print()
    print("Sources retrieved:")
    for s in r1["sources"]:
        page = f"p.{s['page']}" if s.get("page") else "annotation"
        print(f"  [{s['type']:16}] [{s['tag']}] score={s['score']}  {s['source']} {page}")
    print()
    print("Answer:")
    print()
    # Indent the answer for readability
    for line in r1["answer"].splitlines():
        print(f"  {line}")
    print()

    # ── Test 2: Path 2 — Logic query ──────────────────────────────────────────
    q2 = "Build a circuit where pressing I1 turns on Q1 only if I2 is also active"
    print(f"{'─' * 70}")
    print(f"TEST 2 — Path 2  (logic)")
    print(f"Query:  \"{q2}\"")
    print(f"{'─' * 70}")

    r2 = run(q2)

    print(f"Intent:   {r2['intent']}")
    print(f"Time:     {r2['elapsed_seconds']} s")
    print(f"Retries:  {r2['retries']}")
    print(f"Error:    {r2['error']}")
    print()
    print("Examples retrieved:")
    for s in r2["sources"]:
        print(f"  [{s['tag']}] score={s['score']}  {s['source']}")
    print()

    if r2["logojson"]:
        print("LOGO!JSON generated:")
        print()
        print(json.dumps(r2["logojson"], indent=2))
        print()
        print(f"Description: {r2['answer']}")
    else:
        print("LOGO!JSON generation FAILED.")
        print(f"Error: {r2['error']}")
    print()

    # ── Test 3: Path 1 — GATE query ───────────────────────────────────────────
    q3 = "What is the difference between an ON_DELAY timer and a RETENTIVE timer?"
    print(f"{'─' * 70}")
    print(f"TEST 3 — Path 1  (spec — timer comparison)")
    print(f"Query:  \"{q3}\"")
    print(f"{'─' * 70}")

    r3 = run(q3)

    print(f"Intent:   {r3['intent']}")
    print(f"Time:     {r3['elapsed_seconds']} s")
    print(f"Error:    {r3['error']}")
    print()
    print("Sources retrieved:")
    for s in r3["sources"]:
        page = f"p.{s['page']}" if s.get("page") else "annotation"
        print(f"  [{s['type']:16}] [{s['tag']}] score={s['score']}  {s['source']} {page}")
    print()
    print("Answer:")
    print()
    for line in r3["answer"].splitlines():
        print(f"  {line}")
    print()

    print("=" * 70)
    print("Self-test complete.")
    print()
    if all(r["error"] is None for r in [r1, r2, r3]):
        print("All 3 tests passed with no errors.")
    else:
        errors = [(i+1, r["error"]) for i, r in enumerate([r1, r2, r3]) if r["error"]]
        for test_num, err in errors:
            print(f"  Test {test_num} had an error: {err}")
    print()
    print("pipeline.py is ready. You can now build app.py.")
