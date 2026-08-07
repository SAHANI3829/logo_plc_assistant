"""
render.py  —  Turns a validated LOGO!JSON object into human-facing output.

Three public functions:
    render_ladder_svg(logojson) -> str   ladder diagram SVG
    render_fbd_svg(logojson)    -> str   function block diagram SVG
    json_to_st(logojson)        -> str   IEC 61131-3-style Structured Text

Colour conventions match the project's architecture diagrams (see CLAUDE.md):
    gates                                -> green   (#E1F5EE / #0F6E56 / #085041)
    timers, LATCH, PULSE                 -> amber   (#FAEEDA / #854F0B / #412402)
    counters                             -> red     (#FCEBEB / #A32D2D / #501313)
    RISING_EDGE, FALLING_EDGE            -> olive   (#EAF3DE / #3B6D11 / #173404)
    COMPARATOR, OUTPUT                   -> purple  (#EEEDFE / #534AB7 / #26215C)
"""

import xml.sax.saxutils as saxutils

# ── Colour conventions ───────────────────────────────────────────────────────────

_GATE_STYLE    = {"fill": "#E1F5EE", "stroke": "#0F6E56", "text": "#085041"}
_TIMER_STYLE   = {"fill": "#FAEEDA", "stroke": "#854F0B", "text": "#412402"}
_COUNTER_STYLE = {"fill": "#FCEBEB", "stroke": "#A32D2D", "text": "#501313"}
_EDGE_STYLE    = {"fill": "#EAF3DE", "stroke": "#3B6D11", "text": "#173404"}
_PURPLE_STYLE  = {"fill": "#EEEDFE", "stroke": "#534AB7", "text": "#26215C"}   # COMPARATOR + OUTPUT
_WARNING_STYLE = {"fill": "#FEF2F2", "stroke": "#B91C1C", "text": "#7F1D1D"}   # invalid OUTPUT-to-OUTPUT reference

_TYPE_STYLE = {}
for _t in ("AND", "OR", "NOT", "NAND", "NOR", "XOR"):
    _TYPE_STYLE[_t] = _GATE_STYLE
for _t in ("ON_DELAY", "OFF_DELAY", "ON_OFF_DELAY", "RETENTIVE_TIMER", "LATCH", "PULSE"):
    _TYPE_STYLE[_t] = _TIMER_STYLE
for _t in ("UP_COUNTER", "DOWN_COUNTER"):
    _TYPE_STYLE[_t] = _COUNTER_STYLE
for _t in ("RISING_EDGE", "FALLING_EDGE"):
    _TYPE_STYLE[_t] = _EDGE_STYLE
for _t in ("COMPARATOR", "OUTPUT"):
    _TYPE_STYLE[_t] = _PURPLE_STYLE

RAIL_COLOR      = "#0F6E56"
ID_LABEL_COLOR  = "#888780"
WIRE_COLOR      = "#888780"
FONT_FAMILY     = "Arial, sans-serif"

_GATE_TYPES         = {"AND", "OR", "NAND", "NOR", "XOR"}
_SCALAR_TYPES        = {"NOT", "RISING_EDGE", "FALLING_EDGE"}
_SIMPLE_TIMER_TYPES  = {"ON_DELAY", "OFF_DELAY", "PULSE"}
_COUNTER_TYPES       = {"UP_COUNTER", "DOWN_COUNTER"}


def _esc(value) -> str:
    return saxutils.escape(str(value))


def _svg_open(width, height) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="{FONT_FAMILY}">'
    )


def _empty_svg(width, height, message) -> str:
    return (
        f"{_svg_open(width, height)}"
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#FFFFFF"/>'
        f'<text x="{width/2}" y="{height/2}" font-size="12" fill="{ID_LABEL_COLOR}" '
        f'text-anchor="middle">{_esc(message)}</text>'
        f"</svg>"
    )


# ── Shared: which blocks feed a given block, in display order ────────────────────

def _primary_inputs(block: dict) -> list:
    """
    Return [(role, value), ...] for the references this block reads from.
    role is None for a block's single/primary input, or a short prefix
    ("S", "R") when a block has more than one distinctly-named input.
    """
    btype = block.get("type")

    if btype in _GATE_TYPES:
        return [(None, v) for v in block.get("inputs", [])]

    if btype in _SCALAR_TYPES or btype in _SIMPLE_TIMER_TYPES or btype in ("COMPARATOR", "OUTPUT", "ON_OFF_DELAY"):
        return [(None, block.get("input"))]

    if btype == "RETENTIVE_TIMER" or btype in _COUNTER_TYPES:
        return [(None, block.get("input")), ("R", block.get("reset"))]

    if btype == "LATCH":
        return [("S", block.get("set")), ("R", block.get("reset"))]

    return []


def _ladder_line2(block: dict) -> str:
    """The line of text shown inside the ladder box, below the block-type name."""
    if block.get("type") == "OUTPUT":
        return str(block.get("pin", "?"))
    parts = []
    for role, value in _primary_inputs(block):
        parts.append(f"{role}:{value}" if role else str(value))
    return ", ".join(parts)


# ── Ladder diagram ─────────────────────────────────────────────────────────────

def _fan_out_counts(blocks: list, blocks_by_id: dict) -> dict:
    """How many other blocks read each block's value, by id."""
    counts = {}
    for block in blocks:
        for _role, value in _primary_inputs(block):
            if value in blocks_by_id:
                counts[value] = counts.get(value, 0) + 1
    return counts


def _rung_chain(out_block: dict, blocks_by_id: dict, block_index: dict, shared_ids: set) -> tuple:
    """
    Walk backward from an OUTPUT block's input to find every block that feeds
    it. Stops at physical pins (nothing to draw — they're inline labels), at
    any other OUTPUT block (Rule 11 violation that slipped past validation —
    flagged as a warning instead of expanded), and at any *shared* block
    (fed to more than one other block) — shared blocks are drawn once,
    separately, with a branch wire into each rung that uses them, rather than
    being duplicated in every rung.

    Returns (chain, shared_entries):
        chain          -> [("block"|"warning", id), ...] ending with the
                          OUTPUT's own id; never contains a shared block.
        shared_entries -> [(shared_block_id, consumer_id), ...] so the caller
                          can draw a branch wire from the shared block to
                          whichever block (or the OUTPUT itself) reads it
                          within this rung.
    """
    visited  = set()
    warnings = set()
    shared_entries = []
    stack = [(out_block.get("input"), out_block["id"])]
    while stack:
        ref, consumer = stack.pop()
        if ref not in blocks_by_id:
            continue
        block = blocks_by_id[ref]

        if block.get("type") == "OUTPUT":
            visited.add(ref)
            warnings.add(ref)
            continue

        if ref in shared_ids:
            shared_entries.append((ref, consumer))
            continue

        if ref in visited:
            continue
        visited.add(ref)
        for _role, value in _primary_inputs(block):
            if value in blocks_by_id:
                stack.append((value, ref))

    ancestors = sorted(visited, key=lambda bid: block_index[bid])
    chain = [("warning" if bid in warnings else "block", bid) for bid in ancestors]
    chain.append(("block", out_block["id"]))
    return chain, shared_entries


def _draw_ladder_box(parts: list, bx, box_y, block_w: int, block_id: str,
                      block: dict = None, warning: bool = False) -> None:
    """Draw one ladder box (normal or a dashed 'INVALID' warning box) plus its 3 labels."""
    half = block_w / 2
    if warning:
        parts.append(
            f'<rect x="{bx}" y="{box_y}" width="{block_w}" height="40" rx="4" ry="4" '
            f'fill="{_WARNING_STYLE["fill"]}" stroke="{_WARNING_STYLE["stroke"]}" '
            f'stroke-width="1.5" stroke-dasharray="4,3"/>'
        )
        parts.append(
            f'<text x="{bx + half}" y="{box_y - 8}" font-size="9" fill="{ID_LABEL_COLOR}" '
            f'text-anchor="middle">{_esc(block_id)}</text>'
        )
        parts.append(
            f'<text x="{bx + half}" y="{box_y + 17}" font-size="11" font-weight="bold" '
            f'fill="{_WARNING_STYLE["text"]}" text-anchor="middle">INVALID</text>'
        )
        parts.append(
            f'<text x="{bx + half}" y="{box_y + 31}" font-size="9" fill="{_WARNING_STYLE["text"]}" '
            f'text-anchor="middle">{_esc(block_id)} is OUTPUT</text>'
        )
        return

    btype = block.get("type", "")
    style = _TYPE_STYLE.get(btype, _PURPLE_STYLE)
    parts.append(
        f'<rect x="{bx}" y="{box_y}" width="{block_w}" height="40" rx="4" ry="4" '
        f'fill="{style["fill"]}" stroke="{style["stroke"]}" stroke-width="1.5"/>'
    )
    parts.append(
        f'<text x="{bx + half}" y="{box_y - 8}" font-size="9" fill="{ID_LABEL_COLOR}" '
        f'text-anchor="middle">{_esc(block_id)}</text>'
    )
    parts.append(
        f'<text x="{bx + half}" y="{box_y + 17}" font-size="11" font-weight="bold" '
        f'fill="{style["text"]}" text-anchor="middle">{_esc(btype)}</text>'
    )
    parts.append(
        f'<text x="{bx + half}" y="{box_y + 31}" font-size="9" fill="{style["text"]}" '
        f'text-anchor="middle">{_esc(_ladder_line2(block))}</text>'
    )


def render_ladder_svg(logojson: dict) -> str:
    """
    Render a LOGO!JSON circuit as a ladder diagram: one horizontal rung per
    OUTPUT block, each showing (left to right) the chain of blocks feeding
    that output, traced backward through the block references. All rungs
    share one continuous pair of left/right power rails.

    A block referenced by more than one other block (e.g. a LATCH feeding
    two separate OUTPUTs) is drawn exactly once, in its own column on the
    left, with a branch wire running from it to every rung that reads it —
    instead of being duplicated inside each of those rungs.
    """
    blocks = logojson.get("blocks", [])
    if not blocks:
        return _empty_svg(300, 100, "No blocks to render.")

    blocks_by_id = {b.get("id"): b for b in blocks}
    block_index  = {b.get("id"): i for i, b in enumerate(blocks)}
    outputs      = [b for b in blocks if b.get("type") == "OUTPUT"]
    if not outputs:
        return _empty_svg(300, 100, "No OUTPUT blocks to render.")

    fan_out = _fan_out_counts(blocks, blocks_by_id)
    shared_ids = {
        bid for bid, count in fan_out.items()
        if count > 1 and blocks_by_id[bid].get("type") != "OUTPUT"
    }

    rung_data = [_rung_chain(out, blocks_by_id, block_index, shared_ids) for out in outputs]

    BLOCK_W, BLOCK_H = 80, 40
    GAP, SIDE_PAD, RAIL_MARGIN = 50, 40, 30
    TOP_MARGIN, RUNG_SPACING = 20, 90
    RAIL_TOP = TOP_MARGIN

    left_rail_x  = RAIL_MARGIN
    first_box_x  = left_rail_x + SIDE_PAD
    has_shared   = bool(shared_ids)
    shared_col_x = first_box_x
    # When any block is shared, every rung's own chain is shifted one slot
    # right so the shared column has room, keeping every rung's boxes aligned.
    local_start_x = first_box_x + (BLOCK_W + GAP) if has_shared else first_box_x
    bus_x = shared_col_x + BLOCK_W + (GAP / 2) if has_shared else None

    rung_box_xs = [
        [local_start_x + j * (BLOCK_W + GAP) for j in range(len(chain))]
        for chain, _shared_entries in rung_data
    ]
    right_rail_x = max(xs[-1] for xs in rung_box_xs) + BLOCK_W + SIDE_PAD
    width        = right_rail_x + RAIL_MARGIN

    rung_ys     = [TOP_MARGIN + 45 + i * RUNG_SPACING for i in range(len(rung_data))]
    RAIL_BOTTOM = rung_ys[-1] + 45
    height      = RAIL_BOTTOM + 20

    parts = [_svg_open(width, height)]
    parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="#FFFFFF"/>')

    # Power rails — one continuous line each, spanning every rung
    parts.append(f'<line x1="{left_rail_x}" y1="{RAIL_TOP}" x2="{left_rail_x}" y2="{RAIL_BOTTOM}" '
                 f'stroke="{RAIL_COLOR}" stroke-width="2"/>')
    parts.append(f'<line x1="{right_rail_x}" y1="{RAIL_TOP}" x2="{right_rail_x}" y2="{RAIL_BOTTOM}" '
                 f'stroke="{RAIL_COLOR}" stroke-width="2"/>')

    for rung_num, ((chain, shared_entries), box_xs, rung_y) in enumerate(
        zip(rung_data, rung_box_xs, rung_ys), start=1
    ):
        box_y = int(rung_y - BLOCK_H / 2)

        # If this rung is fed by a shared block, its wire starts at the local
        # chain instead of the left rail — the branch wire (drawn later, once
        # all rungs are known) fills in the left_rail_x..local_start_x gap.
        line_start_x = local_start_x if shared_entries else left_rail_x
        parts.append(f'<line x1="{line_start_x}" y1="{rung_y}" x2="{right_rail_x}" y2="{rung_y}" '
                     f'stroke="{RAIL_COLOR}" stroke-width="2"/>')
        parts.append(
            f'<text x="{left_rail_x - 14}" y="{rung_y + 4}" font-size="10" font-weight="bold" '
            f'fill="{ID_LABEL_COLOR}" text-anchor="middle">{rung_num}</text>'
        )

        for bx, (kind, block_id) in zip(box_xs, chain):
            if kind == "warning":
                _draw_ladder_box(parts, bx, box_y, BLOCK_W, block_id, warning=True)
            else:
                _draw_ladder_box(parts, bx, box_y, BLOCK_W, block_id, blocks_by_id[block_id])

    # Shared blocks — drawn once each, positioned at the vertical average of
    # the rungs that read them, with a trunk-and-branch wire to each one.
    if has_shared:
        shared_feeds = {sid: [] for sid in shared_ids}
        for (chain, shared_entries), box_xs, rung_y in zip(rung_data, rung_box_xs, rung_ys):
            chain_ids = [bid for _kind, bid in chain]
            for sid, consumer in shared_entries:
                target_x = box_xs[chain_ids.index(consumer)] if consumer in chain_ids else local_start_x
                shared_feeds[sid].append((rung_y, target_x))

        for sid in sorted(shared_ids, key=lambda bid: block_index[bid]):
            feeds = shared_feeds[sid]
            if not feeds:
                continue
            feed_ys       = [y for y, _x in feeds]
            shared_y      = sum(feed_ys) / len(feed_ys)
            shared_box_y  = int(shared_y - BLOCK_H / 2)
            trunk_top     = min(feed_ys + [shared_y])
            trunk_bottom  = max(feed_ys + [shared_y])

            # Wires first, so the shared box renders on top of them
            parts.append(f'<line x1="{left_rail_x}" y1="{shared_y}" x2="{shared_col_x}" y2="{shared_y}" '
                         f'stroke="{RAIL_COLOR}" stroke-width="2"/>')
            parts.append(f'<line x1="{shared_col_x + BLOCK_W}" y1="{shared_y}" x2="{bus_x}" y2="{shared_y}" '
                         f'stroke="{RAIL_COLOR}" stroke-width="2"/>')
            parts.append(f'<line x1="{bus_x}" y1="{trunk_top}" x2="{bus_x}" y2="{trunk_bottom}" '
                         f'stroke="{RAIL_COLOR}" stroke-width="2"/>')
            for fy, fx in feeds:
                parts.append(f'<line x1="{bus_x}" y1="{fy}" x2="{fx}" y2="{fy}" '
                             f'stroke="{RAIL_COLOR}" stroke-width="2"/>')

            _draw_ladder_box(parts, shared_col_x, shared_box_y, BLOCK_W, sid, blocks_by_id[sid])

    parts.append("</svg>")
    return "\n".join(parts)


# ── Function Block Diagram ───────────────────────────────────────────────────────

def render_fbd_svg(logojson: dict) -> str:
    """
    Render a LOGO!JSON circuit as a function block diagram: each block is a
    box with its input references labelled on the left edge and its output
    (its own id, or the physical pin for an OUTPUT block) labelled on the
    right edge. A wire connects a block's output to every box that reads it.
    """
    blocks = logojson.get("blocks", [])
    n = len(blocks)
    if n == 0:
        return _empty_svg(300, 100, "No blocks to render.")

    BLOCK_W, BLOCK_H = 90, 50
    GAP, X0 = 70, 50
    Y_CENTER = 90
    box_y = int(Y_CENTER - BLOCK_H / 2)
    OUT_ROW_Y  = box_y + 16          # type name + output label share this row
    PIN_TOP    = box_y + 30
    PIN_BOTTOM = box_y + 46

    box_xs = [X0 + i * (BLOCK_W + GAP) for i in range(n)]
    width  = box_xs[-1] + BLOCK_W + 50
    height = 180

    id_to_index = {b.get("id"): i for i, b in enumerate(blocks)}

    # Pre-compute each block's input-pin rows (role, value, y) so wires and
    # labels agree on the same coordinates.
    pin_rows = []
    for block in blocks:
        refs = _primary_inputs(block)
        k = len(refs)
        rows = [
            (role, value, PIN_TOP + (PIN_BOTTOM - PIN_TOP) * (j + 0.5) / k)
            for j, (role, value) in enumerate(refs)
        ] if k else []
        pin_rows.append(rows)

    parts = [_svg_open(width, height)]
    parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="#FFFFFF"/>')

    # Wires first, so boxes are drawn on top of the wire ends
    for i, block in enumerate(blocks):
        bx = box_xs[i]
        for role, value, y in pin_rows[i]:
            if value in id_to_index:
                src_x = box_xs[id_to_index[value]] + BLOCK_W
                parts.append(
                    f'<line x1="{src_x}" y1="{OUT_ROW_Y}" x2="{bx}" y2="{y}" '
                    f'stroke="{WIRE_COLOR}" stroke-width="1.5"/>'
                )

    for i, block in enumerate(blocks):
        btype    = block.get("type", "")
        style    = _TYPE_STYLE.get(btype, _PURPLE_STYLE)
        bx       = box_xs[i]
        block_id = block.get("id", f"B{i + 1}")

        parts.append(
            f'<rect x="{bx}" y="{box_y}" width="{BLOCK_W}" height="{BLOCK_H}" '
            f'fill="{style["fill"]}" stroke="{style["stroke"]}" stroke-width="1.5"/>'
        )
        parts.append(
            f'<text x="{bx + BLOCK_W / 2}" y="{OUT_ROW_Y}" font-size="11" font-weight="bold" '
            f'fill="{style["text"]}" text-anchor="middle">{_esc(btype)}</text>'
        )

        for role, value, y in pin_rows[i]:
            label = f"{role}:{value}" if role else str(value)
            parts.append(
                f'<text x="{bx + 4}" y="{y}" font-size="9" fill="{style["text"]}" '
                f'text-anchor="start">{_esc(label)}</text>'
            )

        out_label = block.get("pin") if btype == "OUTPUT" else block_id
        parts.append(
            f'<text x="{bx + BLOCK_W - 4}" y="{OUT_ROW_Y}" font-size="9" fill="{style["text"]}" '
            f'text-anchor="end">{_esc(out_label)}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


# ── Structured Text ──────────────────────────────────────────────────────────────

_FB_TYPE_MAP = {
    "ON_DELAY":        "TON",
    "OFF_DELAY":       "TOF",
    "RETENTIVE_TIMER": "TONR",
    "UP_COUNTER":      "CTU",
    "DOWN_COUNTER":    "CTD",
    "LATCH":           "SR",
    "PULSE":           "TP",
    "RISING_EDGE":     "R_TRIG",
    "FALLING_EDGE":    "F_TRIG",
}


def _time_literal(value) -> str:
    """LOGO!JSON duration '5s' -> IEC 61131-3 time literal 'T#5s'."""
    return f"T#{value}"


def json_to_st(logojson: dict) -> str:
    """
    Convert a LOGO!JSON circuit into IEC 61131-3-style Structured Text.

    Gates become direct boolean expressions. Timers, counters, LATCH, PULSE,
    and edge detectors become instances of their closest standard IEC
    function block (TON, TOF, TONR, CTU, CTD, SR, TP, R_TRIG, F_TRIG) — LOGO!'s
    ON_OFF_DELAY has no single IEC equivalent, so it is emitted as a chained
    TON feeding a TOF.
    """
    blocks      = logojson.get("blocks", [])
    description = logojson.get("description", "")

    var_decls, fb_decls, body_lines = [], [], []

    for block in blocks:
        bid   = block.get("id")
        btype = block.get("type")

        if btype != "OUTPUT":
            var_decls.append(f"    {bid} : BOOL;")

        if btype in ("AND", "OR", "XOR"):
            joiner = {"AND": " AND ", "OR": " OR ", "XOR": " XOR "}[btype]
            body_lines.append(f"{bid} := {joiner.join(block.get('inputs', []))};")

        elif btype in ("NAND", "NOR"):
            joiner = " AND " if btype == "NAND" else " OR "
            body_lines.append(f"{bid} := NOT ({joiner.join(block.get('inputs', []))});")

        elif btype == "NOT":
            body_lines.append(f"{bid} := NOT {block.get('input')};")

        elif btype == "COMPARATOR":
            body_lines.append(
                f"{bid} := ({block.get('input')} {block.get('operator')} {block.get('threshold')});"
            )

        elif btype in _SIMPLE_TIMER_TYPES:
            fb_type, fb_name = _FB_TYPE_MAP[btype], f"{_FB_TYPE_MAP[btype]}_{bid}"
            fb_decls.append(f"    {fb_name} : {fb_type};")
            body_lines.append(f"{fb_name}(IN := {block.get('input')}, PT := {_time_literal(block.get('T'))});")
            body_lines.append(f"{bid} := {fb_name}.Q;")

        elif btype == "ON_OFF_DELAY":
            fb_on, fb_off = f"TON_{bid}", f"TOF_{bid}"
            fb_decls.append(f"    {fb_on} : TON;")
            fb_decls.append(f"    {fb_off} : TOF;")
            body_lines.append(f"{fb_on}(IN := {block.get('input')}, PT := {_time_literal(block.get('T_on'))});")
            body_lines.append(f"{fb_off}(IN := {fb_on}.Q, PT := {_time_literal(block.get('T_off'))});")
            body_lines.append(f"{bid} := {fb_off}.Q;")

        elif btype == "RETENTIVE_TIMER":
            fb_name = f"TONR_{bid}"
            fb_decls.append(f"    {fb_name} : TONR;")
            body_lines.append(
                f"{fb_name}(IN := {block.get('input')}, R := {block.get('reset')}, "
                f"PT := {_time_literal(block.get('T'))});"
            )
            body_lines.append(f"{bid} := {fb_name}.Q;")

        elif btype in _COUNTER_TYPES:
            fb_type, fb_name = _FB_TYPE_MAP[btype], f"{_FB_TYPE_MAP[btype]}_{bid}"
            fb_decls.append(f"    {fb_name} : {fb_type};")
            clk_param = "CU" if btype == "UP_COUNTER" else "CD"
            body_lines.append(
                f"{fb_name}({clk_param} := {block.get('input')}, R := {block.get('reset')}, "
                f"PV := {block.get('count')});"
            )
            body_lines.append(f"{bid} := {fb_name}.Q;")

        elif btype == "LATCH":
            fb_name = f"SR_{bid}"
            fb_decls.append(f"    {fb_name} : SR;")
            body_lines.append(f"{fb_name}(S1 := {block.get('set')}, R := {block.get('reset')});")
            body_lines.append(f"{bid} := {fb_name}.Q1;")

        elif btype in ("RISING_EDGE", "FALLING_EDGE"):
            fb_type, fb_name = _FB_TYPE_MAP[btype], f"{_FB_TYPE_MAP[btype]}_{bid}"
            fb_decls.append(f"    {fb_name} : {fb_type};")
            body_lines.append(f"{fb_name}(CLK := {block.get('input')});")
            body_lines.append(f"{bid} := {fb_name}.Q;")

        elif btype == "OUTPUT":
            body_lines.append(f"{block.get('pin')} := {block.get('input')};")

        body_lines.append("")

    lines = []
    if description:
        lines.append(f"(* {description} *)")
    lines.append("PROGRAM LOGO_Circuit")
    lines.append("VAR")
    lines.extend(var_decls)
    lines.extend(fb_decls)
    lines.append("END_VAR")
    lines.append("")
    lines.extend(body_lines)
    lines.append("END_PROGRAM")

    return "\n".join(lines).rstrip() + "\n"


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    import sys
    import xml.etree.ElementTree as ET

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 70)
    print("render.py  —  Self-test")
    print("=" * 70)
    print()

    circuit_gate_timer = {
        "description": "Q1 turns on 5 seconds after I1 AND I2 are both active",
        "blocks": [
            {"id": "B1", "type": "AND", "inputs": ["I1", "I2"]},
            {"id": "B2", "type": "ON_DELAY", "input": "B1", "T": "5s"},
            {"id": "B3", "type": "OUTPUT", "input": "B2", "pin": "Q1"},
        ],
    }

    circuit_latch_counter = {
        "description": "A latch feeding an up counter that turns on Q2 once the target count is reached",
        "blocks": [
            {"id": "B1", "type": "LATCH", "set": "I1", "reset": "I2"},
            {"id": "B2", "type": "UP_COUNTER", "input": "B1", "reset": "I3", "count": 10},
            {"id": "B3", "type": "COMPARATOR", "input": "B2", "threshold": 10, "operator": ">="},
            {"id": "B4", "type": "OUTPUT", "input": "B3", "pin": "Q2"},
        ],
    }

    circuit_multi_output = {
        "description": "Three independent outputs: a latch, a counter, and a repeater of the latch's output",
        "blocks": [
            {"id": "B1", "type": "LATCH", "set": "I1", "reset": "I3"},
            {"id": "B2", "type": "UP_COUNTER", "input": "I2", "reset": "I3", "count": 5},
            {"id": "B3", "type": "OUTPUT", "input": "B1", "pin": "Q1"},
            {"id": "B4", "type": "OUTPUT", "input": "B2", "pin": "Q2"},
            {"id": "B5", "type": "OUTPUT", "input": "B3", "pin": "Q3"},
        ],
    }

    circuit_shared_block = {
        "description": "Motor control with shared latch",
        "blocks": [
            {"id": "B1", "type": "LATCH", "set": "I1", "reset": "I2"},
            {"id": "B2", "type": "OUTPUT", "input": "B1", "pin": "Q1"},
            {"id": "B3", "type": "OUTPUT", "input": "B1", "pin": "Q2"},
            {"id": "B4", "type": "ON_DELAY", "input": "B1", "T": "10s"},
            {"id": "B5", "type": "OUTPUT", "input": "B4", "pin": "Q3"},
        ],
    }

    for label, circuit in [
        ("Circuit 1 — AND + ON_DELAY + OUTPUT", circuit_gate_timer),
        ("Circuit 2 — LATCH + UP_COUNTER + COMPARATOR + OUTPUT", circuit_latch_counter),
        ("Circuit 3 — 3 OUTPUT blocks -> 3 ladder rungs", circuit_multi_output),
        ("Circuit 4 — B1 shared across 3 rungs, drawn once", circuit_shared_block),
    ]:
        print("-" * 70)
        print(label)
        print("-" * 70)

        ladder_svg = render_ladder_svg(circuit)
        fbd_svg    = render_fbd_svg(circuit)
        st_code    = json_to_st(circuit)

        # Both SVGs must be well-formed XML — this will raise if not.
        ladder_root = ET.fromstring(ladder_svg)
        ET.fromstring(fbd_svg)

        print(f"Ladder SVG: {ladder_root.get('width')}x{ladder_root.get('height')}, "
              f"{len(ladder_svg)} chars, well-formed XML — OK")
        print(f"FBD SVG:    {len(fbd_svg)} chars, well-formed XML — OK")
        print()
        print("Structured Text:")
        print(st_code)
        print()

    print("=" * 70)
    print("Self-test complete. All SVGs parsed as valid XML.")
