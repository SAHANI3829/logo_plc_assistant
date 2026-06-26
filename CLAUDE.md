# CLAUDE.md — Project Briefing for Claude Code
# RAG-Based LLM Assistant for LOGO! PLC Programming
# Sahani Rajapaksha | FGS/MSc/CS/2025/005 | MSc Computer Science

---

## READ THIS FIRST

This file gives you full context about this MSc research project.
Read it completely before writing any code or making any suggestions.
Every architectural decision here was carefully thought through —
do not suggest changing the core approach without strong reason.

---

## Project Identity

- **Title:** A Retrieval-Augmented Generation Framework for Large Language Model-Assisted PLC Programming
- **Student:** Sahani Rajapaksha
- **ID:** FGS/MSc/CS/2025/005
- **Degree:** MSc in Computer Science
- **Timeline:** June 2026 – October 2026 (20 weeks)
- **Supervisor:** Female supervisor, reviews progress periodically
- **Working style:** Solo researcher, beginner level, needs step-by-step guidance

---

## What This System Does

A chatbot assistant that helps users learn and program Siemens LOGO! 8 PLCs.
User types a natural language question. System retrieves relevant content
from the official LOGO! 8 manual and returns a grounded answer.

For logic/generate queries, the system also generates a LOGO!JSON object
which is rendered as a real SVG ladder diagram, FBD diagram, or ST code.

---

## Tech Stack — DO NOT CHANGE THESE

- **Python:** 3.11.9 (venv at C:\logo_plc_assistant\venv)
- **IDE:** VS Code on Windows (Lenovo T480s)
- **Project folder:** C:\logo_plc_assistant
- **Embedding model:** all-MiniLM-L6-v2 (sentence-transformers)
- **Vector database:** ChromaDB (saved to ./db/)
- **LLM:** GPT-4o-mini via OpenAI API (key in .env file)
- **UI:** Streamlit
- **Installed libraries:** pdfminer.six, sentence-transformers, chromadb, openai, streamlit, python-dotenv

---

## System Architecture — The Two-Path Hybrid RAG

The core design is a dual-path pipeline:

### Path 1 — Spec/Explain path (for questions about specs, explanations, hardware)
```
User query
    → Intent classifier (classify.py) → "spec"
    → ChromaDB retrieval (retrieve.py) → top 3 chunks (filtered by tag)
    → Augmented prompt (query + chunks + "use ONLY this documentation")
    → OpenAI gpt-4o-mini API call
    → Text answer + source citations returned
```

### Path 2 — Logic/Generate path (for building circuits, ladder logic requests)
```
User query
    → Intent classifier (classify.py) → "logic"
    → ChromaDB retrieval (retrieve.py) → top 2 LD/GATE chunks as examples
    → LLM generates LOGO!JSON (closed vocabulary, 18 block types)
    → JSON rule validator (validate.py) → 10 rule checks
    → Repair loop if needed (max 2 retries)
    → Python SVG renderer (render.py)
    → Ladder diagram SVG + FBD diagram SVG + ST code returned
    → User selects which format to view in Streamlit UI
```

---

## LOGO!JSON Format — The Novel Contribution

This is the core innovation of the project. Instead of asking the LLM
to generate XML or raw code, we use a simple closed-vocabulary JSON format.

### Format:
```json
{
  "description": "brief description of the circuit",
  "blocks": [
    {"id": "B1", "type": "AND", "inputs": ["I1", "I2"]},
    {"id": "B2", "type": "ON_DELAY", "input": "B1", "T": "5s"},
    {"id": "B3", "type": "OUTPUT", "input": "B2", "pin": "Q1"}
  ]
}
```

### Allowed block types (CLOSED VOCABULARY — exactly 18):
AND, OR, NOT, NAND, NOR, XOR,
ON_DELAY, OFF_DELAY, ON_OFF_DELAY, RETENTIVE_TIMER,
UP_COUNTER, DOWN_COUNTER,
LATCH, PULSE,
RISING_EDGE, FALLING_EDGE,
COMPARATOR,
OUTPUT

### Rules:
- Input pins: I1–I8 only
- Output pins: Q1–Q4 only
- Block IDs: B1, B2, B3... sequential
- Every circuit MUST end with at least one OUTPUT block
- No block can reference an ID that does not exist

---

## Knowledge Base — Current State

### Source documents:
- Siemens LOGO! 8 System Manual (388 pages PDF) — already processed
- 34 example LOGO! PLC programs (PDFs) — to be added in Week 2-3

### Processing completed:
1. ✅ PDF extracted to data/extracted/pages.json (388 pages)
2. ✅ Text cleaned to data/extracted/cleaned_pages.json (379 chunks)
3. ✅ Chunked to data/chunks/chunks.json (361 chunks — 7 long pages split, 25 tiny fragments dropped)
4. ✅ data/annotations/ladder_annotations.json — 30 hand-crafted LD/GATE circuit examples (all 18 block types covered)
5. ✅ data/annotations/spec_annotations.json — 12 parameter table entries (timers, counters, voltage, memory, temperature)
6. ✅ ChromaDB built at ./db/ — 403 items total (361 manual + 30 ladder + 12 spec)

### Tag system (4 tags) — manual chunks only:
- **SPEC** — hardware specs, wiring, technical data, parameters (158 chunks)
- **GATE** — logic gates, basic functions, special functions (127 chunks)
- **LD** — ladder/circuit diagram content, programming rules (69 chunks)
- **ST** — timing diagrams, status tables, operating states (7 chunks)

### ChromaDB item types (403 total):
- type="manual" — 361 chunks from the LOGO! 8 manual
- type="annotation" — 30 ladder circuit examples (tag=LD or GATE)
- type="spec_annotation" — 12 parameter table entries (tag=SPEC)

### Multi-tag pages:
Some pages have multiple tags and create duplicate chunks for different
retrieval paths. This was a key design decision — page 136 (AND gate)
is tagged both GATE and LD so it can be retrieved by both gate queries
AND ladder queries. Do not remove duplicates — they are intentional.

### Manual section mapping used:
```
Pages 1-13:    SKIP (front matter, TOC)
Pages 14-30:   SPEC (Chapter 1 — getting started)
Pages 31-61:   SPEC (Chapter 2 — installation/wiring)
Pages 62-129:  LD   (Chapter 3 — programming, rules, ladder)
Pages 130-256: GATE (Chapter 4 — logic gates, special functions)
Pages 257-340: SPEC (Chapters 5-13 — cloud, web server, security)
Pages 341-364: SPEC (Appendix A — technical data)
Pages 367-381: SPEC (Appendix D — menu structure)
Page 382:      SPEC (order numbers)
Pages 383+:    SKIP (index)
```

### Precise page overrides (multi-tag):
```
Page 61:  ST + SPEC    (device operating states)
Page 68:  LD           (circuit diagram to LOGO! transition)
Page 69:  LD           (circuit diagram to LOGO! transition)
Page 136: GATE + LD    (AND gate + series circuit equivalent)
Page 137: GATE + ST    (AND edge + timing diagram)
Page 138: GATE         (NAND gate)
Page 139: GATE + LD    (OR gate + parallel circuit)
Page 140: GATE + LD    (NOR gate)
Page 141: GATE + LD    (XOR + NOT + ladder equivalents)
Page 152: GATE + LD + ST (on-delay wiring + timing)
Page 156: GATE + ST    (off-delay timing diagram)
Page 211: GATE + ST    (pulse relay sequential table)
Pages 126-129: SPEC    (software resource specs, memory limits)
Page 382: SPEC         (order numbers)
```

---

## Project Folder Structure

```
C:\logo_plc_assistant\
├── .env                        ← OpenAI API key (NEVER share or commit)
├── CLAUDE.md                   ← this file
├── app.py                      ← Streamlit UI (not built yet)
├── requirements.txt            ← pip freeze output
├── data/
│   ├── raw/
│   │   └── logo8_manual.pdf    ← original manual PDF
│   ├── extracted/
│   │   ├── pages.json          ← raw extracted text (388 pages)
│   │   └── cleaned_pages.json  ← cleaned + tagged chunks (379 chunks)
│   ├── chunks/
│   │   └── chunks.json         ← ✅ DONE — 361 sentence-boundary chunks
│   └── annotations/
│       ├── ladder_annotations.json ← ✅ DONE — 30 LD/GATE circuit examples
│       └── spec_annotations.json   ← ✅ DONE — 12 parameter table entries
├── db/                         ← ✅ DONE — ChromaDB (403 items, cosine similarity)
└── src/
    ├── extract.py              ← ✅ DONE — PDF extraction
    ├── clean.py                ← ✅ DONE — text cleaning + tagging
    ├── chunk.py                ← ✅ DONE — sentence boundary chunking
    ├── embed.py                ← ✅ DONE — embedding + ChromaDB build (3 sources)
    ├── classify.py             ← NEXT — intent classifier
    ├── retrieve.py             ← TODO — ChromaDB retrieval
    ├── pipeline.py             ← TODO — main RAG router
    ├── validate.py             ← TODO — LOGO!JSON validator
    └── render.py               ← TODO — SVG renderer
```

---

## What Is Done vs What Is Next

### ✅ COMPLETED
- Python environment setup (venv, all libraries installed)
- Project folder structure created
- LOGO! 8 manual PDF copied to data/raw/
- src/extract.py — extracts 388 pages from PDF
- src/clean.py — cleans text, assigns tags, creates 379 chunks
- src/chunk.py — sentence-boundary chunking → 361 chunks in data/chunks/chunks.json
- data/annotations/ladder_annotations.json — 30 LD/GATE circuit examples, all 18 block types covered
- data/annotations/spec_annotations.json — 12 parameter table entries (timers, counters, voltage, memory, temperature)
- src/embed.py — embeds all 3 sources, builds ChromaDB at ./db/ with 403 items
- All 9 architecture diagrams created (with Mermaid codes for draw.io)
- Complete methodology designed and documented
- Evaluation framework designed (3 conditions, 30 queries)
- Project checklist spreadsheet with real deadlines created

### 🔄 IMMEDIATE NEXT STEPS (do these in order)
1. Write src/classify.py — intent classifier (spec vs logic)
2. Write src/retrieve.py — ChromaDB retrieval with tag filtering
3. Write src/pipeline.py — main RAG router (ties classify + retrieve + LLM together)
4. Write src/validate.py — LOGO!JSON rule validator (10 rules)
5. Write src/render.py — SVG renderer for LD, FBD, ST output
6. Write app.py — Streamlit UI

### 📅 LATER (Weeks 2-3)
- Add 34 example LOGO! PLC programs to corpus
- These go through same pipeline: extract → clean → chunk → tag → embed
- Tag example programs with extra metadata: type="example"

---

## Evaluation Framework

### Three conditions — same 30 queries each:
- **Condition A:** Plain GPT-4o-mini, no RAG, no manual context
- **Condition B:** Claude Sonnet + Gemini Flash, no RAG (supervisor's request)
- **Condition C:** Full system — GPT-4o-mini + RAG + LOGO!JSON pipeline

### 30 test queries:
- 12 GATE/FBD queries (Easy x4, Medium x5, Hard x3)
- 12 LD queries (Easy x4, Medium x5, Hard x3)
- 6 ST queries (Easy x2, Medium x3, Hard x1)
  Note: ST queries reduced from 10 because LOGO! primarily uses FBD/LD

### Scoring metrics (1-5 scale):
- Accuracy — is the information factually correct per the manual?
- Relevance — does it answer what was asked?
- Clarity — is the explanation clear to a beginner?
- JSON validity rate — % of logic queries that passed validation first try

### Additional evaluation:
- Retrieval precision — manually check top-3 chunks for 10 queries
- Inter-rater reliability — classmate scores 20 responses independently
- Response time measurement — log seconds per query per condition

---

## Thesis Structure

- Chapter 1: Introduction and problem statement
- Chapter 2: Literature review (11 papers reviewed)
- Chapter 3: Methodology — two-path hybrid RAG + LOGO!JSON design
- Chapter 4: Implementation — how each component was built
- Chapter 5: Evaluation — 30 queries, 3 conditions, results table
- Chapter 6: Discussion and limitations
- Chapter 7: Conclusion

### Formal methodology name:
"A Hybrid RAG Framework with Domain-Constrained Structured Generation
for Multi-Paradigm PLC Programming Assistance"

---

## Key Design Decisions — Do Not Change Without Good Reason

1. **ChromaDB over FAISS** — simpler API for beginner, metadata filtering built in
2. **GPT-4o-mini over larger models** — cost effective, reliable, ~$2 total for project
3. **Closed vocabulary LOGO!JSON** — prevents hallucination, enables reliable rendering
4. **Two-path architecture** — separates spec retrieval from logic generation cleanly
5. **4 tags not 3** — ST added as separate tag after NotebookLM analysis revealed
   timing diagrams and status tables need separate retrieval path
6. **Multi-tag pages** — intentional duplicate chunks for pages with mixed content
7. **all-MiniLM-L6-v2** — small, fast, runs on CPU, good for technical English
8. **temperature=0** — deterministic LLM output for reproducible evaluation

---

## Important Notes for Claude Code

- Student is a beginner — explain every piece of code you write
- Always tell her what to type in the terminal, what file to create/edit
- Always explain WHY before HOW — she needs to understand, not just copy-paste
- When writing scripts, keep them simple and well-commented
- If something could break, warn her before running it
- The .env file contains the OpenAI API key — never log it or expose it
- Virtual environment must be activated before running any script:
  Windows: venv\Scripts\activate
- All scripts should be run from the project root: C:\logo_plc_assistant
- The student works on Windows — use Windows path separators where needed

---

## Literature Review Papers (for reference)

1. LLM-based and Retrieval-Augmented Control Code Generation (OSCAT + FAISS + GPT-4)
2. Agents4PLC — multi-agent framework for closed-loop PLC code generation
3. Vendor-Aware Industrial Agents — RAG for Mitsubishi iQ-R PLC
4. LLM4SFC — Sequential Function Chart generation via LLMs
5. STARK — knowledge-augmented framework for ST code (LLaMA 3 70B)
6. Improving LLM-Assisted Secure Code Generation through RAG (all-MiniLM-L6-v2)
7. Spec2Control — automating PLC/DCS control-logic from natural language
8. AutoPLC — vendor-aware ST generation (Claude-3.5-Sonnet backbone)
9. MapCoder — multi-agent code generation for competitive programming
10. RAGdeterm — deterministic RAG using PostgreSQL instead of vector search
11. CircuitMind — multi-agent framework for circuit generation
12. CircuitLM — multi-agent pipeline for circuit schematics from natural language

---

## Current Progress Summary

**Week 1 is nearly complete.**
- Environment fully set up ✅
- Manual extracted and cleaned ✅
- 379 chunks ready with tags ✅
- Next: chunking script then annotations then embedding

The student understands the full system architecture and has been
involved in every design decision. She is engaged and motivated.
Guide her step by step through each remaining task.
