import json
import re
import os

# ── Settings ──────────────────────────────────────────────────────────────────
# We target chunks of ~400 words. This is a good size for embedding:
# long enough to carry context, short enough to be about ONE topic.
TARGET_WORDS = 400

# Chunks shorter than this (after splitting) get discarded.
# Tiny fragments (e.g. just a figure caption) are noise.
MIN_WORDS = 50


# ── Sentence splitter ─────────────────────────────────────────────────────────
def split_into_sentences(text):
    """
    Split a block of text into individual sentences.

    Strategy: look for a period / ! / ? followed by whitespace and then
    either an uppercase letter or a bullet point character.
    This handles most English sentences without needing NLTK.
    """
    # The pattern means: split AFTER  [.!?]  then whitespace  then [A-Z•–]
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z•–—])', text)
    return [s.strip() for s in sentences if s.strip()]


# ── Core chunker ──────────────────────────────────────────────────────────────
def chunk_text(text):
    """
    Split a long text into chunks of roughly TARGET_WORDS words.

    We never cut in the middle of a sentence — we accumulate sentences
    until adding the next one would exceed the target, then we close the
    current chunk and start a new one.
    """
    sentences = split_into_sentences(text)

    chunks = []
    current_sentences = []
    current_word_count = 0

    for sentence in sentences:
        sentence_words = len(sentence.split())

        # If adding this sentence would push us over the target,
        # AND we already have something built up — close the current chunk.
        if current_word_count + sentence_words > TARGET_WORDS and current_sentences:
            chunks.append(" ".join(current_sentences))
            current_sentences = [sentence]
            current_word_count = sentence_words
        else:
            current_sentences.append(sentence)
            current_word_count += sentence_words

    # Don't forget the last batch of sentences
    if current_sentences:
        chunks.append(" ".join(current_sentences))

    return chunks


# ── Main pipeline ─────────────────────────────────────────────────────────────
def chunk_all(input_path, output_path):
    print(f"Reading: {input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        pages = json.load(f)

    print(f"Found {len(pages)} page-level chunks from the clean step.")
    print()

    all_chunks = []
    chunk_counter = 0
    kept_whole = 0
    was_split = 0
    skipped_tiny = 0

    tag_counts = {"SPEC": 0, "GATE": 0, "LD": 0, "ST": 0}

    for page in pages:
        text      = page["text"]
        word_count = len(text.split())

        if word_count <= TARGET_WORDS:
            # ── Short page: keep as one chunk ──────────────────────────────
            if word_count < MIN_WORDS:
                # Page is too short to be useful (just figure labels etc.)
                skipped_tiny += 1
                continue

            chunk_counter += 1
            chunk_id = f"chunk_{chunk_counter:04d}"

            all_chunks.append({
                "chunk_id":   chunk_id,
                "page":       page["page"],
                "tag":        page["tag"],
                "source":     page["source"],
                "text":       text,
                "word_count": word_count
            })
            kept_whole += 1
            tag_counts[page["tag"]] = tag_counts.get(page["tag"], 0) + 1

        else:
            # ── Long page: split at sentence boundaries ─────────────────────
            sub_texts = chunk_text(text)
            was_split += 1

            for i, sub_text in enumerate(sub_texts):
                wc = len(sub_text.split())

                if wc < MIN_WORDS:
                    skipped_tiny += 1
                    continue

                chunk_counter += 1
                chunk_id = f"chunk_{chunk_counter:04d}"

                # Add _part1, _part2 … to source so we know it came from
                # a split page — useful for debugging retrieval later
                source = f"{page['source']}_part{i + 1}"

                all_chunks.append({
                    "chunk_id":   chunk_id,
                    "page":       page["page"],
                    "tag":        page["tag"],
                    "source":     source,
                    "text":       sub_text,
                    "word_count": wc
                })
                tag_counts[page["tag"]] = tag_counts.get(page["tag"], 0) + 1

    # ── Save output ────────────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, indent=2, ensure_ascii=False)

    # ── Print summary ──────────────────────────────────────────────────────────
    print("Done!")
    print()
    print(f"  Input page-chunks:              {len(pages)}")
    print(f"  Pages kept whole (<=400 words): {kept_whole}")
    print(f"  Pages split into sub-chunks:    {was_split}")
    print(f"  Fragments skipped (<50 words):  {skipped_tiny}")
    print(f"  -----------------------------------------")
    print(f"  TOTAL output chunks:            {len(all_chunks)}")
    print()
    print("  Tag breakdown:")
    print(f"    SPEC (hardware/specs):  {tag_counts.get('SPEC', 0)} chunks")
    print(f"    GATE (logic gates):     {tag_counts.get('GATE', 0)} chunks")
    print(f"    LD   (ladder/circuit):  {tag_counts.get('LD', 0)} chunks")
    print(f"    ST   (status/timing):   {tag_counts.get('ST', 0)} chunks")
    print()
    print(f"  Saved to: {output_path}")


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    chunk_all(
        input_path="data/extracted/cleaned_pages.json",
        output_path="data/chunks/chunks.json"
    )
