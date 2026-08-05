"""
validate.py  —  LOGO!JSON structural validator for the LOGO! PLC RAG assistant.

Enforces the 10 rules from CLAUDE.md's closed-vocabulary LOGO!JSON format,
plus Rule 11: an OUTPUT block may not take another OUTPUT block as its input.
Every rule is checked (not just the first failure) so a single call tells
you everything wrong with a circuit — useful both for debugging by hand and
for feeding a rich error_hint back to the LLM in pipeline.py's retry loop.

Public function
----------------
    validate(logojson) -> (is_valid: bool, errors: list[str])

    is_valid, errors = validate(logojson)
    # is_valid is True and errors == []            → circuit is well-formed
    # is_valid is False and errors == ["Rule 6: ...", ...]

Field shapes per block type (confirmed against data/annotations/ladder_annotations.json,
which is what pipeline.py feeds the LLM as few-shot examples):
    AND/OR/NAND/NOR/XOR      -> "inputs": [ref, ref, ...]
    NOT/RISING_EDGE/
      FALLING_EDGE           -> "input": ref
    ON_DELAY/OFF_DELAY/PULSE -> "input": ref, "T": "<n>s"
    ON_OFF_DELAY             -> "input": ref, "T_on": "<n>s", "T_off": "<n>s"
    RETENTIVE_TIMER          -> "input": ref, "reset": ref, "T": "<n>s"
    UP_COUNTER/DOWN_COUNTER  -> "input": ref, "reset": ref, "count": int
    LATCH                    -> "set": ref, "reset": ref
    COMPARATOR               -> "input": ref, "threshold": number, "operator": str
    OUTPUT                   -> "input": ref, "pin": "Q1".."Q4"

"ref" means either a physical input pin (I1-I8) or another block's "id".
"""

import re

# ── Closed vocabulary ───────────────────────────────────────────────────────────

ALLOWED_BLOCK_TYPES = {
    "AND", "OR", "NOT", "NAND", "NOR", "XOR",
    "ON_DELAY", "OFF_DELAY", "ON_OFF_DELAY", "RETENTIVE_TIMER",
    "UP_COUNTER", "DOWN_COUNTER",
    "LATCH", "PULSE",
    "RISING_EDGE", "FALLING_EDGE",
    "COMPARATOR",
    "OUTPUT",
}

INPUT_PINS  = {f"I{i}" for i in range(1, 9)}   # I1-I8
OUTPUT_PINS = {f"Q{i}" for i in range(1, 5)}   # Q1-Q4

GATE_TYPES         = {"AND", "OR", "NAND", "NOR", "XOR"}
SCALAR_REF_TYPES    = {"NOT", "RISING_EDGE", "FALLING_EDGE", "COMPARATOR"}
SIMPLE_TIMER_TYPES  = {"ON_DELAY", "OFF_DELAY", "PULSE"}
COUNTER_TYPES       = {"UP_COUNTER", "DOWN_COUNTER"}

_DURATION_RE  = re.compile(r"^\d+(\.\d+)?s$")     # e.g. "5s", "2.5s"
_PIN_LIKE_RE  = re.compile(r"^I\d+$")             # anything meant to be an input pin


# ── Small helpers ────────────────────────────────────────────────────────────────

def _is_duration(value) -> bool:
    return isinstance(value, str) and bool(_DURATION_RE.match(value))


def _check_ref(value, block_id, field_name, all_ids, defined_ids, errors):
    """
    Validate a single reference value (something that should point at either
    a physical input pin or a previously-defined block id).

    Applies Rule 1 (pin range), Rule 5 (must exist), and Rule 6 (no forward refs).
    """
    if not isinstance(value, str):
        errors.append(
            f"Rule 5: Block {block_id} field '{field_name}' must be a string reference, "
            f"got {type(value).__name__}."
        )
        return

    # Looks like it's trying to be a physical input pin
    if _PIN_LIKE_RE.match(value):
        if value not in INPUT_PINS:
            errors.append(
                f"Rule 1: Block {block_id} field '{field_name}' uses invalid input pin "
                f"'{value}' — only I1-I8 are allowed."
            )
        return

    # Otherwise it must be a block id
    if value not in all_ids:
        errors.append(
            f"Rule 5: Block {block_id} field '{field_name}' references unknown id '{value}' "
            f"— no block with that id exists in this circuit."
        )
        return

    if value not in defined_ids:
        errors.append(
            f"Rule 6: Block {block_id} field '{field_name}' references '{value}', which is "
            f"defined later in (or is) the same circuit — forward references are not allowed."
        )


# ── Public validate function ─────────────────────────────────────────────────────

def validate(logojson) -> tuple:
    """
    Run all 11 structural rules against a LOGO!JSON object.

    Parameters
    ----------
    logojson : the parsed LOGO!JSON dict (already valid JSON — this function
               only checks structure, not JSON syntax).

    Returns
    -------
    (is_valid, errors) : (bool, list[str])
        errors is empty when is_valid is True. Each entry names the rule
        that fired, e.g. "Rule 6: Block B2 field 'input' references 'B3', ..."
    """
    errors = []

    # ── Pre-checks: can't do anything else without a basic shape ─────────────
    if not isinstance(logojson, dict):
        return False, ["Structural: top-level LOGO!JSON must be a JSON object, not a list or string."]

    blocks = logojson.get("blocks")
    if not isinstance(blocks, list) or len(blocks) == 0:
        return False, ["Structural: 'blocks' must be a non-empty list."]

    # ── Rule 3: sequential block IDs (B1, B2, B3 ...) ─────────────────────────
    all_ids = set()
    id_to_block = {}
    for i, block in enumerate(blocks, start=1):
        expected_id = f"B{i}"
        actual_id = block.get("id")
        if actual_id != expected_id:
            errors.append(
                f"Rule 3: Block at position {i} has id '{actual_id}', expected '{expected_id}' "
                f"— block ids must be sequential starting at B1."
            )
        all_ids.add(actual_id)
        id_to_block[actual_id] = block

    # Vocabulary check (not one of the 10 numbered rules, but required before
    # we can apply any per-type field checks below).
    for i, block in enumerate(blocks, start=1):
        btype = block.get("type")
        if btype not in ALLOWED_BLOCK_TYPES:
            errors.append(
                f"Vocabulary: Block {block.get('id', f'#{i}')} has unknown type '{btype}'. "
                f"Allowed types: {', '.join(sorted(ALLOWED_BLOCK_TYPES))}."
            )

    # ── Rule 4: circuit must end with at least one OUTPUT block ───────────────
    output_blocks = [b for b in blocks if b.get("type") == "OUTPUT"]
    if not output_blocks:
        errors.append("Rule 4: Circuit must contain at least one OUTPUT block.")

    # ── Rules 1, 2, 5, 6, 7, 8, 9, 10: walk blocks in order ───────────────────
    # defined_ids accumulates as we go, so a block can only reference ids that
    # appear strictly before it — this is what makes Rule 6 (forward reference)
    # detectable.
    defined_ids = set()

    for i, block in enumerate(blocks, start=1):
        block_id = block.get("id", f"#{i}")
        btype    = block.get("type")

        if btype in GATE_TYPES:
            # Rule 8: gate blocks must use "inputs" as a list, not a single value
            inputs = block.get("inputs")
            if not isinstance(inputs, list) or len(inputs) == 0:
                errors.append(
                    f"Rule 8: Gate block {block_id} ('{btype}') must have 'inputs' as a "
                    f"non-empty list, got {type(inputs).__name__ if inputs is not None else 'missing field'}."
                )
            else:
                for ref in inputs:
                    _check_ref(ref, block_id, "inputs", all_ids, defined_ids, errors)

        elif btype in SCALAR_REF_TYPES:
            # Rule 9: single-reference blocks must use a scalar "input", not a list
            ref = block.get("input")
            if isinstance(ref, list):
                errors.append(
                    f"Rule 9: Block {block_id} ('{btype}') must have a single scalar 'input', "
                    f"not a list."
                )
            elif ref is None:
                errors.append(f"Rule 9: Block {block_id} ('{btype}') is missing required field 'input'.")
            else:
                _check_ref(ref, block_id, "input", all_ids, defined_ids, errors)

            if btype == "COMPARATOR":
                threshold = block.get("threshold")
                operator  = block.get("operator")
                if not isinstance(threshold, (int, float)):
                    errors.append(f"Rule 9: COMPARATOR block {block_id} must have a numeric 'threshold'.")
                if operator not in {"<", "<=", ">", ">=", "==", "!="}:
                    errors.append(
                        f"Rule 9: COMPARATOR block {block_id} has invalid 'operator' '{operator}'."
                    )

        elif btype in SIMPLE_TIMER_TYPES:
            # Rule 7: simple timer blocks need scalar "input" + duration "T"
            ref = block.get("input")
            if ref is None:
                errors.append(f"Rule 7: Timer block {block_id} ('{btype}') is missing required field 'input'.")
            else:
                _check_ref(ref, block_id, "input", all_ids, defined_ids, errors)

            t_val = block.get("T")
            if not _is_duration(t_val):
                errors.append(
                    f"Rule 7: Timer block {block_id} ('{btype}') has invalid duration 'T'='{t_val}' "
                    f"— expected a value like \"5s\"."
                )

        elif btype == "ON_OFF_DELAY":
            # Rule 7: ON_OFF_DELAY uses T_on / T_off instead of a single T
            ref = block.get("input")
            if ref is None:
                errors.append(f"Rule 7: Block {block_id} ('ON_OFF_DELAY') is missing required field 'input'.")
            else:
                _check_ref(ref, block_id, "input", all_ids, defined_ids, errors)

            for field in ("T_on", "T_off"):
                if not _is_duration(block.get(field)):
                    errors.append(
                        f"Rule 7: Block {block_id} ('ON_OFF_DELAY') has invalid duration "
                        f"'{field}'='{block.get(field)}' — expected a value like \"5s\"."
                    )

        elif btype == "RETENTIVE_TIMER":
            # Rule 7: input + reset + T
            for field in ("input", "reset"):
                ref = block.get(field)
                if ref is None:
                    errors.append(f"Rule 7: Block {block_id} ('RETENTIVE_TIMER') is missing required field '{field}'.")
                else:
                    _check_ref(ref, block_id, field, all_ids, defined_ids, errors)

            if not _is_duration(block.get("T")):
                errors.append(
                    f"Rule 7: Block {block_id} ('RETENTIVE_TIMER') has invalid duration "
                    f"'T'='{block.get('T')}' — expected a value like \"30s\"."
                )

        elif btype in COUNTER_TYPES:
            # Rule 7: input + reset + integer count
            for field in ("input", "reset"):
                ref = block.get(field)
                if ref is None:
                    errors.append(f"Rule 7: Counter block {block_id} ('{btype}') is missing required field '{field}'.")
                else:
                    _check_ref(ref, block_id, field, all_ids, defined_ids, errors)

            count = block.get("count")
            if not isinstance(count, int) or isinstance(count, bool):
                errors.append(
                    f"Rule 7: Counter block {block_id} ('{btype}') must have an integer 'count', "
                    f"got {count!r}."
                )

        elif btype == "LATCH":
            # Rule 9: LATCH uses scalar "set" and "reset", not a list, not "input"
            for field in ("set", "reset"):
                ref = block.get(field)
                if isinstance(ref, list):
                    errors.append(f"Rule 9: LATCH block {block_id} field '{field}' must be scalar, not a list.")
                elif ref is None:
                    errors.append(f"Rule 9: LATCH block {block_id} is missing required field '{field}'.")
                else:
                    _check_ref(ref, block_id, field, all_ids, defined_ids, errors)

        elif btype == "OUTPUT":
            # Rule 10: OUTPUT must have both "input" and a valid "pin"
            ref = block.get("input")
            if ref is None:
                errors.append(f"Rule 10: OUTPUT block {block_id} is missing required field 'input'.")
            else:
                _check_ref(ref, block_id, "input", all_ids, defined_ids, errors)

                # Rule 11: an OUTPUT block cannot take another OUTPUT block as its input.
                ref_block = id_to_block.get(ref)
                if ref_block is not None and ref_block.get("type") == "OUTPUT":
                    errors.append(
                        f"Rule 11: OUTPUT block {block_id} references another OUTPUT block "
                        f"'{ref}' as its input — each OUTPUT must be driven by its own "
                        f"dedicated logic/function block chain, not another OUTPUT block."
                    )

            pin = block.get("pin")
            if pin not in OUTPUT_PINS:
                errors.append(
                    f"Rule 2: OUTPUT block {block_id} uses invalid output pin '{pin}' "
                    f"— only Q1-Q4 are allowed."
                )

        # Unknown types were already reported above under "Vocabulary" —
        # nothing further to check for them here.

        # This block's own id is now considered "defined" for all blocks after it.
        defined_ids.add(block.get("id"))

    return len(errors) == 0, errors


# ── Self-test ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    import json
    import sys

    # Windows terminals default to the cp1252 codepage, which mangles the
    # em-dash used in this file's messages. Force UTF-8 for clean output.
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 70)
    print("validate.py  —  Self-test")
    print("=" * 70)
    print()

    # ── Case 1: valid circuit — should pass all 10 rules ──────────────────────
    valid_circuit = {
        "description": "Q1 turns on 5 seconds after I1 AND I2 are both active",
        "blocks": [
            {"id": "B1", "type": "AND", "inputs": ["I1", "I2"]},
            {"id": "B2", "type": "ON_DELAY", "input": "B1", "T": "5s"},
            {"id": "B3", "type": "OUTPUT", "input": "B2", "pin": "Q1"},
        ],
    }

    # ── Case 2: forward reference — B1 references B2, which is defined AFTER it ──
    forward_ref_circuit = {
        "description": "Broken: B1 references B2 before B2 exists",
        "blocks": [
            {"id": "B1", "type": "NOT", "input": "B2"},
            {"id": "B2", "type": "AND", "inputs": ["I1", "I2"]},
            {"id": "B3", "type": "OUTPUT", "input": "B1", "pin": "Q1"},
        ],
    }

    # ── Case 3: wrong pin names — I9 (out of range) and Q9 (out of range) ────────
    wrong_pins_circuit = {
        "description": "Broken: invalid input and output pins",
        "blocks": [
            {"id": "B1", "type": "AND", "inputs": ["I1", "I9"]},
            {"id": "B2", "type": "OUTPUT", "input": "B1", "pin": "Q9"},
        ],
    }

    # ── Case 4: OUTPUT referencing another OUTPUT — B5's input is B3, an OUTPUT ──
    output_to_output_circuit = {
        "description": "Broken: 3 outputs, but B5 references B3 (itself an OUTPUT) as its input",
        "blocks": [
            {"id": "B1", "type": "LATCH", "set": "I1", "reset": "I3"},
            {"id": "B2", "type": "UP_COUNTER", "input": "I2", "reset": "I3", "count": 5},
            {"id": "B3", "type": "OUTPUT", "input": "B1", "pin": "Q1"},
            {"id": "B4", "type": "OUTPUT", "input": "B2", "pin": "Q2"},
            {"id": "B5", "type": "OUTPUT", "input": "B3", "pin": "Q3"},
        ],
    }

    cases = [
        ("Case 1 — valid circuit (should PASS)", valid_circuit),
        ("Case 2 — forward reference (Rule 6)", forward_ref_circuit),
        ("Case 3 — wrong pin names (Rules 1 & 2)", wrong_pins_circuit),
        ("Case 4 — OUTPUT references another OUTPUT (Rule 11)", output_to_output_circuit),
    ]

    for label, circuit in cases:
        print("-" * 70)
        print(label)
        print("-" * 70)
        print(json.dumps(circuit, indent=2))
        print()
        is_valid, errors = validate(circuit)
        print(f"is_valid = {is_valid}")
        if errors:
            print("Rules fired:")
            for e in errors:
                print(f"  - {e}")
        else:
            print("No rule violations — circuit is well-formed.")
        print()

    print("=" * 70)
    print("Self-test complete.")
