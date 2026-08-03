"""
app.py  —  Streamlit chat interface for the LOGO! PLC RAG assistant.

Ties together the whole pipeline for an end user:
    user types a question or circuit request
        -> pipeline.run() classifies it, retrieves context, calls GPT-4o-mini
        -> spec queries   render as an answer + an expandable sources panel
        -> logic queries  render as a LOGO!JSON circuit, viewable as
                           Ladder / FBD / Structured Text

Run with:
    streamlit run app.py
"""

import os
import re
import sys

import streamlit as st

# Make src/ importable regardless of the working directory Streamlit was launched from.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from pipeline import run
from render import render_ladder_svg, render_fbd_svg, json_to_st


st.set_page_config(page_title="LOGO! PLC Programming Assistant", layout="centered")

st.title("LOGO! PLC Programming Assistant")
st.caption(
    "Ask a question about Siemens LOGO! 8 (specs, wiring, gates, timers) or "
    "describe a circuit you want built."
)

if "messages" not in st.session_state:
    st.session_state.messages = []   # [{"role": "user", "text": ...}] or [{"role": "assistant", "result": ...}]


_SVG_HEIGHT_RE = re.compile(r'height="(\d+)"')


def _svg_natural_height(svg: str, default: int = 160) -> int:
    match = _SVG_HEIGHT_RE.search(svg)
    return int(match.group(1)) if match else default


def _render_spec_message(result: dict) -> None:
    st.markdown(result["answer"])
    if result.get("error"):
        st.caption(f"Note: {result['error']}")

    sources = result.get("sources", [])
    with st.expander(f"Sources ({len(sources)})"):
        for i, src in enumerate(sources, start=1):
            location = f"Manual p.{src['page']}" if src.get("page") else src.get("source", "knowledge base")
            st.markdown(
                f"**[{i}]** `{src['tag']}` · {src['type']} · similarity={src['score']:.2f} · {location}"
            )


def _render_logic_message(result: dict, key_prefix: str) -> None:
    logojson = result.get("logojson")

    if not logojson:
        st.error(f"Could not generate a valid circuit: {result.get('error', 'unknown error')}")
        return

    st.markdown(f"**{result['answer']}**")
    if result.get("retries"):
        st.caption(f"Needed {result['retries']} retry attempt(s) to pass validation.")

    view = st.radio(
        "View as",
        ("Ladder", "FBD", "Structured Text"),
        key=f"{key_prefix}_view",
        horizontal=True,
    )

    if view == "Ladder":
        svg = render_ladder_svg(logojson)
        st.image(svg)
    elif view == "FBD":
        svg = render_fbd_svg(logojson)
        st.image(svg)
    else:
        st.code(json_to_st(logojson), language="text")


def _render_assistant_message(result: dict, key_prefix: str) -> None:
    if result["intent"] == "spec":
        _render_spec_message(result)
    else:
        _render_logic_message(result, key_prefix)


# ── Replay conversation history ───────────────────────────────────────────────
for i, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"]):
        if message["role"] == "user":
            st.markdown(message["text"])
        else:
            _render_assistant_message(message["result"], key_prefix=f"msg_{i}")


# ── Handle new input ───────────────────────────────────────────────────────────
prompt = st.chat_input("Ask a question or describe a circuit...")

if prompt:
    st.session_state.messages.append({"role": "user", "text": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Working on it..."):
            result = run(prompt)
        _render_assistant_message(result, key_prefix=f"msg_{len(st.session_state.messages)}")

    st.session_state.messages.append({"role": "assistant", "result": result})
