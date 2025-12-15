"""
Script to annotate PDF with evaluation comments.
Reads evaluation JSON and places comments on the PDF at specified coordinates.
Also draws red underlines from OCR data (Gemini output).
"""

import os
import random
import fitz  # PyMuPDF


def draw_tick_mark(page, x: float, y: float, size: float = 12, color: tuple = (0, 0.6, 0), width: float = 2):
    """
    Draw a tick mark (checkmark ✓) at the specified position.

    Args:
        page: PyMuPDF page object
        x: X coordinate (left edge of tick mark)
        y: Y coordinate (vertical center of tick mark)
        size: Size of the tick mark
        color: RGB color tuple (default: green)
        width: Line width
    """
    shape = page.new_shape()

    # Proper checkmark shape: ✓
    # Short line going down-right, then long line going up-right
    # Start point (top of short stroke)
    p1 = fitz.Point(x, y)
    # Bottom point (where both strokes meet)
    p2 = fitz.Point(x + size * 0.3, y + size * 0.5)
    # End point (top of long stroke)
    p3 = fitz.Point(x + size, y - size * 0.4)

    # Draw the checkmark as two connected lines
    shape.draw_line(p1, p2)
    shape.draw_line(p2, p3)

    shape.finish(color=color, width=width, lineCap=1, lineJoin=1)  # Round caps and joins
    shape.commit()


def draw_tick_marks_on_pages(doc, num_ticks_per_page: int = 4) -> int:
    """
    Draw tick marks at random positions on each page.

    Avoids margins:
    - Top 10%
    - Bottom 10%
    - Left 10%
    - Right 20% (where annotations go)

    Args:
        doc: PyMuPDF document object
        num_ticks_per_page: Number of tick marks per page (default: 4)

    Returns:
        Total number of tick marks drawn
    """
    tick_count = 0
    GREEN_COLOR = (0, 0.6, 0)  # Green color for tick marks

    for page_num in range(len(doc)):
        page = doc[page_num]
        page_width = page.rect.width
        page_height = page.rect.height

        # Calculate safe zone (avoiding margins)
        # Left 10%, Right 20%, Top 10%, Bottom 10%
        safe_x_min = page_width * 0.10
        safe_x_max = page_width * 0.80  # 100% - 20% right margin
        safe_y_min = page_height * 0.10
        safe_y_max = page_height * 0.90

        # Generate random positions for tick marks
        for i in range(num_ticks_per_page):
            x = random.uniform(safe_x_min, safe_x_max - 15)  # -15 for tick mark width
            y = random.uniform(safe_y_min, safe_y_max)

            draw_tick_mark(page, x, y, size=12, color=GREEN_COLOR, width=2)
            tick_count += 1

        print(f"Drew {num_ticks_per_page} tick marks on page {page_num + 1}")

    return tick_count


def draw_underlines_from_ocr(doc, ocr_data: dict, pages_metadata: list) -> int:
    """
    Draw red underlines on the PDF based on Gemini OCR output.

    Args:
        doc: PyMuPDF document object
        ocr_data: OCR output dictionary containing Underlines for each page
        pages_metadata: List of page metadata for coordinate conversion

    Returns:
        Number of underlines drawn
    """
    underline_count = 0
    RED_COLOR = (0.8, 0, 0)  # Red color for underlines

    # Create metadata lookup by page number
    metadata_by_page = {m.page_number: m for m in pages_metadata} if pages_metadata else {}

    # Normalize ocr_data if it's a list
    if isinstance(ocr_data, list):
        if len(ocr_data) == 1 and isinstance(ocr_data[0], dict):
            ocr_data = ocr_data[0]
        elif len(ocr_data) > 0 and isinstance(ocr_data[0], dict) and "Page_Number" in ocr_data[0]:
            ocr_data = {"Pages": ocr_data}

    for page_data in ocr_data.get("Pages", []):
        page_num = page_data.get("Page_Number", 1) - 1  # Convert to 0-indexed

        if page_num < 0 or page_num >= len(doc):
            continue

        page = doc[page_num]
        underlines = page_data.get("Underlines", [])

        for underline in underlines:
            coords = underline.get("coordinates", [])
            text = underline.get("text", "")

            if len(coords) != 4:
                continue

            x1, y1, x2, y2 = coords

            # Convert normalized coordinates to PDF points
            # Check if we have metadata for this page
            metadata = metadata_by_page.get(page_num + 1)  # 1-indexed

            if metadata:
                # Use metadata for proper conversion
                # Normalized coords are 0-1, convert to page dimensions
                page_width = metadata.original_width_pt
                page_height = metadata.original_height_pt
            else:
                # Fallback to current page dimensions
                page_width = page.rect.width
                page_height = page.rect.height

            # Convert normalized to points
            x1_pt = x1 * page_width
            y1_pt = y1 * page_height
            x2_pt = x2 * page_width
            y2_pt = y2 * page_height

            # Draw red underline (horizontal line)
            shape = page.new_shape()
            shape.draw_line(fitz.Point(x1_pt, y2_pt), fitz.Point(x2_pt, y2_pt))
            shape.finish(color=RED_COLOR, width=2)  # Bold red line
            shape.commit()

            underline_count += 1
            print(f"Drew underline for '{text}' on page {page_num + 1}")

    return underline_count


def annotate_pdf_with_comments(pdf_path: str, evaluation: dict, output_path: str,
                                ocr_data: dict = None, pages_metadata: list = None) -> str:
    """
    Annotate PDF with evaluation comments and red underlines.

    This function:
    1. First draws RED underlines from Gemini OCR output (if provided)
    2. Then adds text annotations from OpenAI evaluation in the right margin

    Args:
        pdf_path: Path to the original PDF
        evaluation: Evaluation dictionary with comments
        output_path: Path for output PDF
        ocr_data: OCR output dict containing Underlines array (optional)
        pages_metadata: List of page metadata for coordinate conversion (optional)

    Returns:
        Path to the annotated PDF
    """
    # Open PDF
    doc = fitz.open(pdf_path)

    # Add 2.5 inch right margin to each page (2.5 * 72 = 180 points)
    RIGHT_MARGIN = 180

    print("Adding 2.5 inch right margin to each page...")
    for page_num in range(len(doc)):
        page = doc[page_num]
        current_rect = page.rect
        new_width = current_rect.width + RIGHT_MARGIN
        new_rect = fitz.Rect(0, 0, new_width, current_rect.height)
        page.set_mediabox(new_rect)

    print(f"Added {RIGHT_MARGIN} points (2.5 inches) right margin to all pages.")

    # ===========================================
    # STEP 1: Draw RED underlines from OCR data
    # ===========================================
    underline_count = 0
    if ocr_data:
        print("Drawing red underlines from Gemini OCR data...")
        underline_count = draw_underlines_from_ocr(doc, ocr_data, pages_metadata)
        print(f"Drew {underline_count} red underlines.")

    # ===========================================
    # STEP 2: Draw GREEN tick marks at random positions
    # ===========================================
    print("Drawing tick marks on each page...")
    tick_count = draw_tick_marks_on_pages(doc, num_ticks_per_page=4)
    print(f"Drew {tick_count} tick marks total.")

    # ===========================================
    # STEP 3: Add text annotations from evaluation
    # ===========================================
    # Colors
    RED_COLOR = (0.8, 0, 0)

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
                box_width = 170
                chars_per_line = int(box_width / 9)  # Adjusted for larger font
                num_lines = max(1, len(comment_text) // chars_per_line + 1)
                box_height = num_lines * 20 + 15  # Adjusted for larger line spacing

                # Position the comment box in the right margin
                page_width = page.rect.width
                page_height = page.rect.height

                original_page_width = page_width - RIGHT_MARGIN
                box_x1 = original_page_width + 5
                box_y1 = y1
                box_x2 = box_x1 + box_width
                box_y2 = y1 + box_height

                # Ensure box fits within page height
                if box_y2 > page_height - 5:
                    box_y1 = max(5, page_height - box_height - 5)
                    box_y2 = box_y1 + box_height

                # Create the comment box rectangle
                comment_rect = fitz.Rect(box_x1, box_y1, box_x2, box_y2)

                # No box drawn - transparent background, no border

                # Insert text inside the box
                font_path = os.path.join(os.path.dirname(__file__), "PatrickHand-Regular.ttf")

                # Wrap text manually
                words = comment_text.split()
                lines = []
                current_line = ""

                for word in words:
                    test_line = current_line + " " + word if current_line else word
                    if len(test_line) * 9 < box_width - 10:
                        current_line = test_line
                    else:
                        if current_line:
                            lines.append(current_line)
                        current_line = word

                if current_line:
                    lines.append(current_line)

                # Insert each line
                y_offset = box_y1 + 20
                for line in lines:
                    if y_offset + 10 < box_y2:
                        page.insert_text(
                            fitz.Point(box_x1 + 5, y_offset),
                            line,
                            fontsize=16,
                            color=RED_COLOR,
                            fontname="patrickhand",
                            fontfile=font_path
                        )
                        y_offset += 20

                annotation_count += 1
                print(f"Added: {q_id} {section} on page {page_num + 1}")

    # ===========================================
    # STEP 4: Add Summary at bottom of each question's last page
    # ===========================================
    font_path = os.path.join(os.path.dirname(__file__), "PatrickHand-Regular.ttf")
    SUMMARY_COLOR = (0.8, 0, 0)  # Red color for summary (changed from blue)
    summary_count = 0

    for q_id, q_data in questions.items():
        summary_text = q_data.get("Summary", "")
        pages_info = q_data.get("Pages", {})
        last_page = pages_info.get("last")

        if not summary_text or not last_page:
            continue

        # Convert to 0-indexed page number
        try:
            page_num = int(last_page) - 1
        except (ValueError, TypeError):
            continue

        if page_num < 0 or page_num >= len(doc):
            continue

        page = doc[page_num]
        page_height = page.rect.height
        page_width = page.rect.width

        # Position summary at bottom of page (avoiding bottom 5% margin)
        summary_y = page_height - 60  # 60 points from bottom
        summary_x = 50  # Left margin

        # Add "Summary:" label
        page.insert_text(
            fitz.Point(summary_x, summary_y),
            f"{q_id} Summary:",
            fontsize=17,  # Increased from 14 to 17
            color=SUMMARY_COLOR,
            fontname="patrickhand",
            fontfile=font_path
        )

        # Wrap and add summary text
        max_width = page_width - 100  # Leave margins
        words = summary_text.split()
        lines = []
        current_line = ""

        for word in words:
            test_line = current_line + " " + word if current_line else word
            if len(test_line) * 8 < max_width:  # Adjusted for larger font
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word

        if current_line:
            lines.append(current_line)

        # Insert summary lines
        y_offset = summary_y + 20  # Increased spacing for larger font
        for line in lines:
            if y_offset < page_height - 10:
                page.insert_text(
                    fitz.Point(summary_x + 10, y_offset),
                    line,
                    fontsize=15,  # Increased from 12 to 15
                    color=SUMMARY_COLOR,
                    fontname="patrickhand",
                    fontfile=font_path
                )
                y_offset += 18  # Increased line spacing

        summary_count += 1
        print(f"Added summary for {q_id} on page {page_num + 1}")

    # ===========================================
    # STEP 5: Add OverallSummary on a new blank A4 page
    # ===========================================
    overall_summary = evaluation.get("OverallSummary", [])

    if overall_summary:
        # Create a new A4 page at the end
        # A4 dimensions: 595 x 842 points
        new_page = doc.new_page(width=595, height=842)
        print("Added new A4 page for Overall Summary")

        # Title
        title_y = 60
        new_page.insert_text(
            fitz.Point(50, title_y),
            "Overall Summary & Recommendations",
            fontsize=22,
            color=(0.1, 0.1, 0.5),  # Dark blue
            fontname="patrickhand",
            fontfile=font_path
        )

        # Draw a line under title
        shape = new_page.new_shape()
        shape.draw_line(fitz.Point(50, title_y + 10), fitz.Point(545, title_y + 10))
        shape.finish(color=(0.1, 0.1, 0.5), width=2)
        shape.commit()

        # Add bullet points
        y_offset = title_y + 50
        bullet_color = (0.2, 0.2, 0.2)  # Dark gray for text

        for i, item in enumerate(overall_summary, 1):
            # Draw bullet point (filled circle)
            bullet_x = 60
            bullet_y = y_offset - 4

            shape = new_page.new_shape()
            shape.draw_circle(fitz.Point(bullet_x, bullet_y), 4)
            shape.finish(color=(0, 0.5, 0), fill=(0, 0.5, 0))  # Green bullet
            shape.commit()

            # Wrap text for bullet point
            max_width = 480
            words = item.split()
            lines = []
            current_line = ""

            for word in words:
                test_line = current_line + " " + word if current_line else word
                if len(test_line) * 8 < max_width:
                    current_line = test_line
                else:
                    if current_line:
                        lines.append(current_line)
                    current_line = word

            if current_line:
                lines.append(current_line)

            # Insert bullet text
            for j, line in enumerate(lines):
                text_x = bullet_x + 15 if j == 0 else bullet_x + 15
                new_page.insert_text(
                    fitz.Point(text_x, y_offset),
                    line,
                    fontsize=14,
                    color=bullet_color,
                    fontname="patrickhand",
                    fontfile=font_path
                )
                y_offset += 20

            y_offset += 15  # Extra space between bullet points

        print(f"Added {len(overall_summary)} bullet points to Overall Summary page")

    # Save the annotated PDF
    doc.save(output_path)
    doc.close()

    print(f"\n{'=' * 60}")
    print(f"✅ Annotated PDF saved to: {output_path}")
    print(f"📝 Total annotations added: {annotation_count}")
    print(f"📍 Total underlines drawn: {underline_count}")
    print(f"✓ Total tick marks drawn: {tick_count}")
    print(f"📋 Total summaries added: {summary_count}")
    print(f"📄 Overall Summary page: {'Added' if overall_summary else 'N/A'}")
    print("=" * 60)

    return output_path

