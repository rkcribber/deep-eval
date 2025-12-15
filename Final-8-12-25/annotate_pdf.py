"""
Script to annotate PDF with evaluation comments.
Reads evaluation JSON and places comments on the PDF at specified coordinates.
Uses the _verified.pdf (with OCR rectangles) as the base.

Usage:
  python annotate_pdf.py <original_pdf> <evaluation_json>

Example:
  python annotate_pdf.py 53545.pdf 53545_evaluation.json
"""

import sys
import json
import os
import fitz  # PyMuPDF


def annotate_pdf_with_comments(pdf_path: str, evaluation_json_path: str, output_path: str = None):
    """
    Annotate PDF with evaluation comments from JSON.

    Uses the _verified.pdf version as the base (with OCR coordinate rectangles).
    Each comment from Introduction, Body, Conclusion sections will be placed
    at the specified page and coordinates with red text inside a blue box.

    Args:
        pdf_path: Path to the original PDF (will look for _verified.pdf version)
        evaluation_json_path: Path to the evaluation JSON file
        output_path: Path for output PDF (default: adds '_annotated' suffix)
    """
    # Use original PDF as base (not verified)
    source_pdf_path = pdf_path
    print(f"Using original PDF as base: {pdf_path}")

    # Load evaluation JSON
    with open(evaluation_json_path, "r") as f:
        evaluation = json.load(f)

    # Open PDF (verified or original)
    doc = fitz.open(source_pdf_path)

    # Add 2.5 inch right margin to each page (2.5 * 72 = 180 points)
    RIGHT_MARGIN = 180  # 2.5 inches in points

    print("Adding 2.5 inch right margin to each page...")
    for page_num in range(len(doc)):
        page = doc[page_num]
        # Get current page dimensions
        current_rect = page.rect
        # Create new rectangle with extended width
        new_width = current_rect.width + RIGHT_MARGIN
        new_rect = fitz.Rect(0, 0, new_width, current_rect.height)
        # Set the new page size (this extends the page to the right)
        page.set_mediabox(new_rect)

    print(f"Added {RIGHT_MARGIN} points (2.5 inches) right margin to all pages.")

    # Output path
    if output_path is None:
        output_path = pdf_path.replace(".pdf", "_annotated.pdf")

    # Colors
    BLUE_COLOR = (0, 0, 0.8)      # Blue for box border
    BLUE_FILL = (0.9, 0.95, 1)    # Light blue fill
    RED_COLOR = (0.8, 0, 0)       # Red for text

    # Shift amount for blue boxes (to the right)
    BOX_SHIFT_RIGHT = 25

    annotation_count = 0

    # Process each question
    questions = evaluation.get("Questions", {})

    for q_id, q_data in questions.items():
        comments = q_data.get("Comments", {})

        # Process Introduction, Body, Conclusion
        for section in ["Introduction", "Body", "Conclusion"]:
            section_comments = comments.get(section, [])

            for comment_data in section_comments:
                comment_text = comment_data.get("comment", "")
                page_num = comment_data.get("page", 1) - 1  # Convert to 0-indexed
                coordinates = comment_data.get("coordinates", [])

                if not comment_text or len(coordinates) != 4:
                    continue

                if page_num < 0 or page_num >= len(doc):
                    print(f"Warning: Page {page_num + 1} out of range for {q_id} {section}")
                    continue

                page = doc[page_num]
                x1, y1, x2, y2 = coordinates

                # Create a comment box in the right margin
                # 2.5 inch margin = 180 points, box width should fit within it
                box_width = 170  # Fixed width to fit within 180pt margin (with 5pt padding on each side)

                # Dynamic height based on text length (no max limit)
                chars_per_line = int(box_width / 8)  # Approx chars per line at font 14
                num_lines = max(1, len(comment_text) // chars_per_line + 1)
                box_height = num_lines * 18 + 15  # Dynamic height based on content

                # Position the comment box in the right margin
                # Place box at the right edge of the page within the margin
                page_width = page.rect.width
                page_height = page.rect.height

                # Box starts 5pt from right edge of original content area (before margin was added)
                original_page_width = page_width - RIGHT_MARGIN
                box_x1 = original_page_width + 5  # 5pt padding from original content
                box_y1 = y1
                box_x2 = box_x1 + box_width
                box_y2 = y1 + box_height

                # Ensure box fits within page height
                if box_y2 > page_height - 5:
                    box_y1 = max(5, page_height - box_height - 5)
                    box_y2 = box_y1 + box_height

                # Create the comment box rectangle
                comment_rect = fitz.Rect(box_x1, box_y1, box_x2, box_y2)

                # Draw blue filled rectangle with border
                shape = page.new_shape()
                shape.draw_rect(comment_rect)
                shape.finish(color=BLUE_COLOR, fill=BLUE_FILL, width=1.5)
                shape.commit()

                # Insert text inside the box
                text_rect = fitz.Rect(box_x1 + 3, box_y1 + 3, box_x2 - 3, box_y2 - 3)

                # Use text writer for better control
                font_path = os.path.join(os.path.dirname(__file__), "PatrickHand-Regular.ttf")

                # Wrap text manually
                words = comment_text.split()
                lines = []
                current_line = ""

                for word in words:
                    test_line = current_line + " " + word if current_line else word
                    if len(test_line) * 9 < box_width - 10:  # Approximate character width for font 16
                        current_line = test_line
                    else:
                        if current_line:
                            lines.append(current_line)
                        current_line = word

                if current_line:
                    lines.append(current_line)

                # Insert each line (comment text only, no label)
                y_offset = box_y1 + 18  # Start from top of box
                for line in lines:  # No line limit - height is dynamic
                    if y_offset + 10 < box_y2:
                        page.insert_text(
                            fitz.Point(box_x1 + 5, y_offset),
                            line,
                            fontsize=14,
                            color=RED_COLOR,
                            fontname="patrickhand",
                            fontfile=font_path
                        )
                        y_offset += 18  # Increased line spacing for larger font


                annotation_count += 1
                print(f"Added: {q_id} {section} on page {page_num + 1}")

    # Save the annotated PDF
    doc.save(output_path)
    doc.close()

    print(f"\n{'=' * 60}")
    print(f"✅ Annotated PDF saved to: {output_path}")
    print(f"📝 Total annotations added: {annotation_count}")
    print("=" * 60)

    return output_path


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        print("\nThis script annotates a PDF with evaluation comments.")
        print("Comments are placed at coordinates specified in the evaluation JSON.")
        sys.exit(1)

    pdf_path = sys.argv[1]
    evaluation_json_path = sys.argv[2]
    output_path = sys.argv[3] if len(sys.argv) > 3 else None

    annotate_pdf_with_comments(pdf_path, evaluation_json_path, output_path)


if __name__ == "__main__":
    main()

