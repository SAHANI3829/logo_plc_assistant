import json
import os
import chromadb
from sentence_transformers import SentenceTransformer

# ── File paths ─────────────────────────────────────────────────────────────────
CHUNKS_PATH           = "data/chunks/chunks.json"
ANNOTATIONS_PATH      = "data/annotations/ladder_annotations.json"
SPEC_ANNOTATIONS_PATH = "data/annotations/spec_annotations.json"
DB_PATH               = "./db"
COLLECTION_NAME       = "logo_plc"

# ── Embedding model ────────────────────────────────────────────────────────────
# all-MiniLM-L6-v2 converts text to a 384-dimensional vector.
# It is small (90 MB), runs on CPU, and works well for technical English.
MODEL_NAME = "all-MiniLM-L6-v2"


def load_data():
    """Read the manual chunks, ladder annotations, and spec annotations from disk."""
    with open(CHUNKS_PATH, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    with open(ANNOTATIONS_PATH, "r", encoding="utf-8") as f:
        ladder_annotations = json.load(f)
    with open(SPEC_ANNOTATIONS_PATH, "r", encoding="utf-8") as f:
        spec_annotations = json.load(f)
    return chunks, ladder_annotations, spec_annotations


def embed_texts(model, texts, label):
    """
    Embed a list of strings and return a plain Python list of vectors.

    We convert the numpy array to a list because ChromaDB expects plain Python.
    """
    print(f"  Embedding {len(texts)} {label}...")
    vectors = model.encode(texts, batch_size=32, show_progress_bar=False)
    print(f"  Done. Each vector has {vectors.shape[1]} dimensions.")
    return vectors.tolist()


def build_database():
    # ── Step 1: Load the embedding model ──────────────────────────────────────
    print("=" * 60)
    print("Step 1 — Loading embedding model")
    print("=" * 60)
    print(f"Model: {MODEL_NAME}")
    print("Note: The first run downloads ~90 MB and caches it locally.")
    print("      Subsequent runs load from cache and are much faster.")
    print()
    model = SentenceTransformer(MODEL_NAME)
    print("Model loaded successfully.")
    print()

    # ── Step 2: Load data ──────────────────────────────────────────────────────
    print("=" * 60)
    print("Step 2 — Loading data files")
    print("=" * 60)
    chunks, ladder_annotations, spec_annotations = load_data()
    print(f"  Manual chunks:       {len(chunks)}  (from {CHUNKS_PATH})")
    print(f"  Ladder annotations:  {len(ladder_annotations)}  (from {ANNOTATIONS_PATH})")
    print(f"  Spec annotations:    {len(spec_annotations)}  (from {SPEC_ANNOTATIONS_PATH})")
    print()

    # ── Step 3: Set up ChromaDB ────────────────────────────────────────────────
    print("=" * 60)
    print("Step 3 — Setting up ChromaDB")
    print("=" * 60)
    os.makedirs(DB_PATH, exist_ok=True)
    client = chromadb.PersistentClient(path=DB_PATH)

    # If a collection already exists from a previous run, delete it cleanly.
    # This ensures we always have a fresh, consistent database.
    try:
        client.delete_collection(COLLECTION_NAME)
        print(f"  Deleted existing collection '{COLLECTION_NAME}' for clean rebuild.")
    except Exception:
        pass

    # cosine similarity is the standard metric for semantic text search.
    collection = client.create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    print(f"  Created collection: '{COLLECTION_NAME}'")
    print(f"  Database folder:    {DB_PATH}")
    print()

    # ── Step 4: Embed and store manual chunks ──────────────────────────────────
    print("=" * 60)
    print("Step 4 — Embedding manual chunks")
    print("=" * 60)

    chunk_ids    = [c["chunk_id"] for c in chunks]
    chunk_texts  = [c["text"] for c in chunks]

    # Metadata stored alongside each embedding in ChromaDB.
    # We can filter by any of these fields during retrieval.
    chunk_metas  = [
        {
            "type":       "manual",   # distinguishes manual from annotations
            "page":       c["page"],
            "tag":        c["tag"],   # SPEC / GATE / LD / ST
            "source":     c["source"],
            "word_count": c["word_count"],
        }
        for c in chunks
    ]

    chunk_vectors = embed_texts(model, chunk_texts, "manual chunks")
    collection.add(
        ids=chunk_ids,
        embeddings=chunk_vectors,
        documents=chunk_texts,
        metadatas=chunk_metas,
    )
    print(f"  {len(chunks)} manual chunks stored in ChromaDB.")
    print()

    # ── Step 5: Embed and store ladder annotations ────────────────────────────
    print("=" * 60)
    print("Step 5 — Embedding ladder annotations (LD/GATE circuits)")
    print("=" * 60)
    print("  Strategy: embed query + text together for richer semantic matching.")
    print()

    ladder_ids   = [a["id"] for a in ladder_annotations]
    ladder_texts = [a["query"] + " " + a["text"] for a in ladder_annotations]

    # The logojson dict must be serialised to a string because ChromaDB
    # metadata values must be plain strings, integers, or floats.
    ladder_metas = [
        {
            "type":     "annotation",
            "tag":      a["tag"],
            "source":   a["source"],
            "query":    a["query"],
            "logojson": json.dumps(a["logojson"]),
        }
        for a in ladder_annotations
    ]

    ladder_vectors = embed_texts(model, ladder_texts, "ladder annotations")
    collection.add(
        ids=ladder_ids,
        embeddings=ladder_vectors,
        documents=ladder_texts,
        metadatas=ladder_metas,
    )
    print(f"  {len(ladder_annotations)} ladder annotations stored in ChromaDB.")
    print()

    # ── Step 6: Embed and store spec annotations ──────────────────────────────
    print("=" * 60)
    print("Step 6 — Embedding spec annotations (parameter tables)")
    print("=" * 60)
    print("  Strategy: embed query + text together for richer semantic matching.")
    print()

    spec_ids   = [a["id"] for a in spec_annotations]
    spec_texts = [a["query"] + " " + a["text"] for a in spec_annotations]
    spec_metas = [
        {
            "type":   "spec_annotation",
            "tag":    a["tag"],     # always "SPEC"
            "source": a["source"],
            "query":  a["query"],
        }
        for a in spec_annotations
    ]

    spec_vectors = embed_texts(model, spec_texts, "spec annotations")
    collection.add(
        ids=spec_ids,
        embeddings=spec_vectors,
        documents=spec_texts,
        metadatas=spec_metas,
    )
    print(f"  {len(spec_annotations)} spec annotations stored in ChromaDB.")
    print()

    # ── Step 7: Verify total count ─────────────────────────────────────────────
    total = collection.count()
    expected = len(chunks) + len(ladder_annotations) + len(spec_annotations)
    print("=" * 60)
    print("Step 7 — Verification")
    print("=" * 60)
    print(f"  Manual chunks:       {len(chunks)}")
    print(f"  Ladder annotations:  {len(ladder_annotations)}")
    print(f"  Spec annotations:    {len(spec_annotations)}")
    print(f"  -----------------------------------------")
    print(f"  Total in ChromaDB:   {total}  (expected {expected})")
    if total == expected:
        print("  Count matches. Database built correctly.")
    else:
        print("  WARNING: count mismatch — check for errors above.")
    print()

    # ── Step 8: Smoke test ────────────────────────────────────────────────────
    # Run three real queries to confirm all three data sources retrieve correctly.
    print("=" * 60)
    print("Step 8 — Smoke test (3 sample queries)")
    print("=" * 60)
    print()

    # Test A — Spec question (should retrieve SPEC manual chunks)
    q_spec = "How many digital inputs does the LOGO! 8 base module have?"
    q_spec_vec = model.encode([q_spec]).tolist()

    results_spec = collection.query(
        query_embeddings=q_spec_vec,
        n_results=3,
        where={"$and": [
            {"type": {"$eq": "manual"}},
            {"tag":  {"$eq": "SPEC"}},
        ]},
    )
    print(f"  Spec query: '{q_spec}'")
    print(f"  Top 3 SPEC chunks retrieved:")
    for doc, meta in zip(results_spec["documents"][0], results_spec["metadatas"][0]):
        print(f"    Page {meta['page']:>3} [{meta['tag']}]  {doc[:75]}...")
    print()

    # Test B — Logic question (should retrieve matching annotations)
    q_logic = "create a circuit to turn on a light 5 seconds after pressing a button"
    q_logic_vec = model.encode([q_logic]).tolist()

    results_logic = collection.query(
        query_embeddings=q_logic_vec,
        n_results=2,
        where={"type": {"$eq": "annotation"}},
    )
    print(f"  Logic query: '{q_logic}'")
    print(f"  Top 2 annotations retrieved:")
    for doc, meta in zip(results_logic["documents"][0], results_logic["metadatas"][0]):
        print(f"    [{meta['tag']}]  {meta['query'][:70]}...")
    print()

    # Test C — Spec parameter question (should retrieve spec_annotations)
    q_param = "What is the maximum timer value I can set in LOGO! 8?"
    q_param_vec = model.encode([q_param]).tolist()

    results_param = collection.query(
        query_embeddings=q_param_vec,
        n_results=2,
        where={"type": {"$eq": "spec_annotation"}},
    )
    print(f"  Param query: '{q_param}'")
    print(f"  Top 2 spec annotations retrieved:")
    for doc, meta in zip(results_param["documents"][0], results_param["metadatas"][0]):
        print(f"    [{meta['tag']}]  {meta['query'][:70]}...")
    print()

    print("Smoke test complete.")
    print()
    print("=" * 60)
    print("ChromaDB is ready. You can now build classify.py and retrieve.py.")
    print("=" * 60)


if __name__ == "__main__":
    build_database()
