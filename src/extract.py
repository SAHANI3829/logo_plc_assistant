from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer
import json
import os

def extract_pdf(pdf_path, output_path):
    print(f"Reading: {pdf_path}")
    pages = []
    
    for page_num, page_layout in enumerate(extract_pages(pdf_path)):
        page_text = ""
        
        for element in page_layout:
            if isinstance(element, LTTextContainer):
                page_text += element.get_text()
        
        cleaned = page_text.strip()
        
        if cleaned:  # only save pages that have text
            pages.append({
                "page": page_num + 1,
                "text": cleaned
            })
        
        # show progress every 50 pages
        if (page_num + 1) % 50 == 0:
            print(f"  Processed {page_num + 1} pages...")
    
    # save to output file
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(pages, f, indent=2, ensure_ascii=False)
    
    print(f"Done! Extracted {len(pages)} pages")
    print(f"Saved to: {output_path}")
    return pages

if __name__ == "__main__":
    extract_pdf(
        pdf_path="data/raw/logo8_manual.pdf",
        output_path="data/extracted/pages.json"
    )