"""
retrieve.py  —  ChromaDB retrieval for the LOGO! PLC RAG assistant.

Public function
---------------
    retrieve(query, n_results=3, tag=None, type_filter=None) -> list[dict]

How pipeline.py will call this:

    Path 1 (spec):
        retrieve(query, n_results=3, tag="SPEC")
        # returns manual SPEC chunks + spec_annotation entries

    Path 2 (logic):
        retrieve(query, n_results=2, type_filter="annotation")
        # returns the closest ladder circuit examples
"""

import chromadb
from sentence_transformers import SentenceTransformer

# ── Config ─────────────────────────────────────────────────────────────────────
DB_PATH         = "./db"
COLLECTION_NAME = "logo_plc"
MODEL_NAME      = "all-MiniLM-L6-v2"

# ── Lazy-loaded singletons ─────────────────────────────────────────────────────
#
# The sentence-transformer model takes a few seconds to load.
# We load it once on the first call and reuse it for every subsequent call.
# This makes the second query much faster than the first.

_model      = None
_collection = None


def _get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def _get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=DB_PATH)
        _collection = client.get_collection(COLLECTION_NAME)
    return _collection


# ── Filter builder ─────────────────────────────────────────────────────────────

def _build_where(tag=None, type_filter=None):
    """
    Build a ChromaDB 'where' clause from optional tag and type filters.

    Examples
    --------
    tag="SPEC"                         → {"tag": {"$eq": "SPEC"}}
    type_filter="annotation"           → {"type": {"$eq": "annotation"}}
    tag="SPEC", type_filter="manual"   → {"$and": [...both...]}
    neither                            → None  (search all 403 items)
    """
    conditions = []
    if tag:
        conditions.append({"tag":  {"$eq": tag}})
    if type_filter:
        conditions.append({"type": {"$eq": type_filter}})

    if len(conditions) == 0:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


# ── Public retrieve function ───────────────────────────────────────────────────

def retrieve(
    query: str,
    n_results: int = 3,
    tag: str = None,
    type_filter: str = None,
) -> list[dict]:
    """
    Search ChromaDB for the most semantically similar chunks to the query.

    Parameters
    ----------
    query       : The user's natural language question or request.
    n_results   : How many results to return. Default is 3.
    tag         : Optional — filter by tag: "SPEC", "GATE", "LD", or "ST".
    type_filter : Optional — filter by item type:
                    "manual"          → only LOGO! 8 manual chunks
                    "annotation"      → only hand-crafted ladder circuit examples
                    "spec_annotation" → only hand-crafted parameter table entries

    Returns
    -------
    List of dicts, sorted by similarity (most similar first).
    Each dict contains:
        text   : the full text of the chunk
        tag    : "SPEC", "GATE", "LD", or "ST"
        source : source identifier (e.g. "logo8_manual_p17" or "anno_009")
        page   : manual page number as int, or None for annotation items
        type   : "manual", "annotation", or "spec_annotation"
        score  : cosine similarity — float from 0.0 to 1.0
                 (higher = more relevant; 0.8+ is a strong match)
    """
    model      = _get_model()
    collection = _get_collection()

    # Embed the query using the SAME model used to embed chunks at build time.
    # This is critical — if we used a different model, the vectors would be
    # in a different space and similarity scores would be meaningless.
    query_vector = model.encode([query]).tolist()

    # Build optional metadata filter
    where = _build_where(tag=tag, type_filter=type_filter)

    # Run the ChromaDB similarity search
    query_kwargs = {
        "query_embeddings": query_vector,
        "n_results":        n_results,
        "include":          ["documents", "metadatas", "distances"],
    }
    if where is not None:
        query_kwargs["where"] = where

    raw = collection.query(**query_kwargs)

    # Package raw results into clean, readable dicts
    docs      = raw["documents"][0]
    metas     = raw["metadatas"][0]
    distances = raw["distances"][0]

    results = []
    for doc, meta, dist in zip(docs, metas, distances):
        # ChromaDB stores cosine DISTANCE (lower = more similar).
        # We convert to SIMILARITY (higher = more similar) for clarity.
        # Formula: similarity = 1 - distance
        score = round(1.0 - dist, 4)

        results.append({
            "text":   doc,
            "tag":    meta.get("tag",    ""),
            "source": meta.get("source", ""),
            "page":   meta.get("page"),          # int for manual; absent for annotations
            "type":   meta.get("type",   ""),
            "score":  score,
        })

    return results


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    print("=" * 65)
    print("retrieve.py  —  Self-test")
    print("=" * 65)
    print("Loading model and ChromaDB (first call takes a few seconds)...")
    print()

    # ── Test 1: Path 1 spec retrieval ─────────────────────────────────────────
    q1 = "What are the digital input voltage levels for LOGO! 8?"
    print(f"Test 1 — Spec query (tag=SPEC, n=3)")
    print(f"  Query: '{q1}'")
    r1 = retrieve(q1, n_results=3, tag="SPEC")
    for i, chunk in enumerate(r1, 1):
        pg = f"p{chunk['page']}" if chunk['page'] else "annotation"
        print(f"  [{i}] score={chunk['score']:.4f}  [{chunk['type']:16}]  [{chunk['tag']}]  {pg}")
        print(f"       {chunk['text'][:90]}...")
    print()

    # ── Test 2: Path 2 logic retrieval ────────────────────────────────────────
    q2 = "Build a circuit that turns on a light 5 seconds after pressing a button"
    print(f"Test 2 — Logic query (type=annotation, n=2)")
    print(f"  Query: '{q2}'")
    r2 = retrieve(q2, n_results=2, type_filter="annotation")
    for i, chunk in enumerate(r2, 1):
        print(f"  [{i}] score={chunk['score']:.4f}  [{chunk['type']:16}]  [{chunk['tag']}]  {chunk['source']}")
        print(f"       {chunk['text'][:90]}...")
    print()

    # ── Test 3: Spec annotation retrieval ─────────────────────────────────────
    q3 = "What is the maximum time I can set on an on-delay timer?"
    print(f"Test 3 — Parameter query (type=spec_annotation, n=2)")
    print(f"  Query: '{q3}'")
    r3 = retrieve(q3, n_results=2, type_filter="spec_annotation")
    for i, chunk in enumerate(r3, 1):
        print(f"  [{i}] score={chunk['score']:.4f}  [{chunk['type']:16}]  [{chunk['tag']}]  {chunk['source']}")
        print(f"       {chunk['text'][:90]}...")
    print()

    # ── Test 4: No filter (searches all 403 items) ────────────────────────────
    q4 = "How does the LATCH block work in LOGO! 8?"
    print(f"Test 4 — No filter, n=3 (searches all 403 items)")
    print(f"  Query: '{q4}'")
    r4 = retrieve(q4, n_results=3)
    for i, chunk in enumerate(r4, 1):
        pg = f"p{chunk['page']}" if chunk['page'] else chunk['source']
        print(f"  [{i}] score={chunk['score']:.4f}  [{chunk['type']:16}]  [{chunk['tag']}]  {pg}")
        print(f"       {chunk['text'][:90]}...")
    print()

    # ── Test 5: Tag + type combined filter ────────────────────────────────────
    q5 = "ON_DELAY timer wiring and timing diagram"
    print(f"Test 5 — Combined filter (tag=LD, type=manual, n=2)")
    print(f"  Query: '{q5}'")
    r5 = retrieve(q5, n_results=2, tag="LD", type_filter="manual")
    for i, chunk in enumerate(r5, 1):
        print(f"  [{i}] score={chunk['score']:.4f}  [{chunk['type']:16}]  [{chunk['tag']}]  p{chunk['page']}")
        print(f"       {chunk['text'][:90]}...")
    print()

    print("Self-test complete.")
