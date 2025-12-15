"""
Verification script to draw rectangles on PDF using OCR coordinates.
This helps visually verify if the coordinates are accurate.

Usage:
  python verify_coords.py <pdf_file> <json_output_file>

Example:
  python verify_coords.py document.pdf document_output.json
"""

import sys
import json
import fitz  # PyMuPDF


def draw_rectangles(pdf_path: str, json_path: str, output_path: str = None):
    """
    Draw rectangles on PDF based on coordinates from OCR JSON output.

    Args:
        pdf_path: Path to the original PDF
        json_path: Path to the OCR JSON output file
        output_path: Path for output PDF (default: adds '_verified' suffix)
    """
    # Load OCR JSON
    with open(json_path, "r") as f:
        ocr_data = json.load(f)

    # Open PDF
    doc = fitz.open(pdf_path)

    # Output path
    if output_path is None:
        output_path = pdf_path.replace(".pdf", "_verified.pdf")

    # Colors for different block types
    colors = {
        "PARAGRAPH": (1, 0, 0),      # Red
        "LIST_ITEM": (0, 1, 0),      # Green
        "FORMULA_BLOCK": (0, 0, 1),  # Blue
        "default": (1, 0.5, 0),      # Orange
    }

    rect_count = 0

    for page_data in ocr_data.get("Pages", []):
        page_num = page_data.get("Page_Number", 1) - 1  # Convert to 0-indexed

        if page_num >= len(doc):
            print(f"Warning: Page {page_num + 1} not found in PDF")
            continue

        page = doc[page_num]
        page_width = page.rect.width
        page_height = page.rect.height

        print(f"\nPage {page_num + 1} (size: {page_width:.1f} x {page_height:.1f} pt)")

        for block in page_data.get("Blocks", []):
            block_num = block.get("Block_Number", "?")

            for line in block.get("Lines", []):
                coords = line.get("Coordinates", [])
                text = line.get("text", "")[:50]  # First 50 chars
                block_type = line.get("block_type", "default")

                if len(coords) != 4:
                    print(f"  Warning: Invalid coordinates for block {block_num}: {coords}")
                    continue

                x1, y1, x2, y2 = coords

                # Check if coordinates are normalized (0-1) or absolute
                is_normalized = all(0 <= c <= 1 for c in coords)

                if is_normalized:
                    # Convert normalized to absolute PDF coordinates
                    x1_abs = x1 * page_width
                    y1_abs = y1 * page_height
                    x2_abs = x2 * page_width
                    y2_abs = y2 * page_height
                    coord_type = "normalized"
                else:
                    # Use as-is (already in points)
                    x1_abs, y1_abs, x2_abs, y2_abs = x1, y1, x2, y2
                    coord_type = "absolute"

                # Create rectangle
                rect = fitz.Rect(x1_abs, y1_abs, x2_abs, y2_abs)

                # Get color based on block type
                color = colors.get(block_type, colors["default"])

                # Draw rectangle
                page.draw_rect(rect, color=color, width=1.5)

                # Add small label with block number
                label_point = fitz.Point(x1_abs, y1_abs - 2)
                page.insert_text(label_point, f"B{block_num}", fontsize=6, color=color)

                rect_count += 1
                print(f"  Block {block_num}: [{x1:.3f}, {y1:.3f}, {x2:.3f}, {y2:.3f}] ({coord_type}) -> [{x1_abs:.1f}, {y1_abs:.1f}, {x2_abs:.1f}, {y2_abs:.1f}]")
                print(f"    Text: {text}...")

    # Save output
    doc.save(output_path)
    doc.close()

    print(f"\n{'=' * 60}")
    print(f"✅ Verified PDF saved to: {output_path}")
    print(f"📊 Total rectangles drawn: {rect_count}")
    print(f"\nColor legend:")
    print(f"  🔴 Red    = PARAGRAPH")
    print(f"  🟢 Green  = LIST_ITEM")
    print(f"  🔵 Blue   = FORMULA_BLOCK")
    print(f"  🟠 Orange = Other/Unknown")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        print("\nThis script draws rectangles on a PDF using coordinates from OCR output.")
        print("Open the output PDF to visually verify if coordinates are correct.")
        sys.exit(1)

    pdf_path = sys.argv[1]
    json_path = sys.argv[2]
    output_path = sys.argv[3] if len(sys.argv) > 3 else None

    draw_rectangles(pdf_path, json_path, output_path)


if __name__ == "__main__":
    main()

