import json
import re
import os

def clean_page(text):
    lines = text.split("\n")
    cleaned_lines = []
    
    skip_patterns = [
        r"^LOGO!$",
        r"^LOGO! $",
        r"System Manual",
        r"A5E\d+",
        r"^\d{1,3}$",
        r"^\s*$",
        r"08/202[0-9]",
    ]
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        skip = False
        for pattern in skip_patterns:
            if re.search(pattern, line):
                skip = True
                break
        if not skip:
            cleaned_lines.append(line)
    
    text = " ".join(cleaned_lines)
    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    return text

# ── Precise page-level tag mapping ───────────────────────────
# Based on full NotebookLM analysis of LOGO! 8 manual
# Format: page_number -> [list of tags]
# Pages not listed get their chapter default tag

PRECISE_PAGE_TAGS = {
    # ST: Device operating states
    61:  ["ST", "SPEC"],
    366: ["ST", "SPEC"],
    
    # LD: Ladder/circuit diagram transitions
    68:  ["LD"],
    69:  ["LD"],
    
    # GATE + LD combined pages (logic gate AND ladder equivalent)
    136: ["GATE", "LD"],   # AND gate + series circuit
    137: ["GATE", "ST"],   # AND edge + timing diagram
    138: ["GATE"],         # NAND / NAND edge
    139: ["GATE", "LD"],   # OR gate + parallel circuit
    140: ["GATE", "LD"],   # NOR gate + series NOR
    141: ["GATE", "LD"],   # XOR + NOT + ladder equivalents
    
    # ST: Special function timing/status diagrams
    152: ["GATE", "LD", "ST"],  # On-delay wiring + timing
    156: ["GATE", "ST"],        # Off-delay timing diagram
    211: ["GATE", "ST"],        # Pulse relay sequential table
    
    # SPEC: Software resource specs
    126: ["SPEC"],
    127: ["SPEC"],
    128: ["SPEC"],
    129: ["SPEC"],
    
    # SPEC: Order numbers
    382: ["SPEC"],
}

# ── Chapter-level default tags ────────────────────────────────
PAGE_MAP = [
    # (start, end, keep, default_tag, description)
    (1,   13,  False, None,   "Front matter — preface and TOC — skip"),
    (14,  30,  True,  "SPEC", "Chapter 1 — getting started, hardware, I/O overview"),
    (31,  61,  True,  "SPEC", "Chapter 2 — installation and wiring"),
    (62,  129, True,  "LD",   "Chapter 3 — programming, rules, ladder transitions"),
    (130, 256, True,  "GATE", "Chapter 4 — logic gates and special functions"),
    (257, 340, True,  "SPEC", "Chapters 5-13 — cloud, web server, security"),
    (341, 364, True,  "SPEC", "Appendix A — technical data, voltage, current"),
    (365, 366, True,  "ST",   "Operating states page"),
    (367, 381, True,  "SPEC", "Appendix D — menu structure"),
    (382, 382, True,  "SPEC", "Order numbers spec page"),
    (383, 999, False, None,   "Back matter — index — skip"),
]

def get_chapter_info(page_num):
    """Get default keep/tag from chapter mapping."""
    for start, end, keep, tag, desc in PAGE_MAP:
        if start <= page_num <= end:
            return keep, tag, desc
    return False, None, "unknown — skip"

def get_tags_for_page(page_num):
    """
    Get the best tags for a page.
    Precise mapping takes priority over chapter default.
    """
    keep, default_tag, desc = get_chapter_info(page_num)
    
    if not keep:
        return False, [], desc
    
    # Use precise tags if available, otherwise use chapter default
    if page_num in PRECISE_PAGE_TAGS:
        tags = PRECISE_PAGE_TAGS[page_num]
    else:
        tags = [default_tag] if default_tag else ["SPEC"]
    
    return True, tags, desc

def clean_all_pages(input_path, output_path):
    print(f"Reading: {input_path}")
    
    with open(input_path, "r", encoding="utf-8") as f:
        pages = json.load(f)
    
    print(f"Processing {len(pages)} pages...")
    print()
    
    # We may create multiple chunks per page if it has multiple tags
    all_chunks = []
    skipped_nav   = 0
    skipped_empty = 0
    kept_pages    = 0
    
    tag_counts = {"SPEC": 0, "GATE": 0, "LD": 0, "ST": 0}
    
    for page in pages:
        page_num = page["page"]
        keep, tags, desc = get_tags_for_page(page_num)
        
        if not keep:
            skipped_nav += 1
            continue
        
        cleaned_text = clean_page(page["text"])
        
        if len(cleaned_text) < 50:
            skipped_empty += 1
            continue
        
        kept_pages += 1
        
        if len(tags) == 1:
            # Single tag — one chunk
            all_chunks.append({
                "page":   page_num,
                "text":   cleaned_text,
                "tag":    tags[0],
                "source": f"logo8_manual_p{page_num}"
            })
            if tags[0] in tag_counts:
                tag_counts[tags[0]] += 1
        
        else:
            # Multiple tags — create one chunk per tag
            # This way the page can be retrieved via any of its tags
            for tag in tags:
                all_chunks.append({
                    "page":   page_num,
                    "text":   cleaned_text,
                    "tag":    tag,
                    "source": f"logo8_manual_p{page_num}_{tag}"
                })
                if tag in tag_counts:
                    tag_counts[tag] += 1
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, indent=2, ensure_ascii=False)
    
    print(f"Done!")
    print(f"  Pages processed:        {kept_pages}")
    print(f"  Total chunks created:   {len(all_chunks)}")
    print(f"  (multi-tag pages create multiple chunks)")
    print()
    print(f"  Skipped (navigation):   {skipped_nav}")
    print(f"  Skipped (too short):    {skipped_empty}")
    print()
    print(f"  Tag breakdown:")
    print(f"    SPEC (hardware/specs):  {tag_counts['SPEC']} chunks")
    print(f"    GATE (logic gates):     {tag_counts['GATE']} chunks")
    print(f"    LD   (ladder/circuit):  {tag_counts['LD']} chunks")
    print(f"    ST   (status/timing):   {tag_counts['ST']} chunks")
    print()
    print(f"  Saved to: {output_path}")

if __name__ == "__main__":
    clean_all_pages(
        input_path="data/extracted/pages.json",
        output_path="data/extracted/cleaned_pages.json"
    )