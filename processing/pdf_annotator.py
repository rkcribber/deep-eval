#!/usr/bin/env python3
"""
PDF Annotation Processor

Adds text annotations to a PDF based on annotation data.
Uses Patrick Hand font for handwriting-style annotations.
"""

import os
import uuid
import random
import logging
import fitz  # PyMuPDF
import requests
import urllib.request

# Get logger for this module
logger = logging.getLogger(__name__)

# Font configuration
FONT_SIZE_OVERRIDE = 16
FONT_NAME = "PatrickHand"
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/patrickhand/PatrickHand-Regular.ttf"


def get_patrick_hand_font():
    """Download Patrick Hand font if not present and return the font object."""
    font_dir = os.path.dirname(os.path.abspath(__file__))
    font_path = os.path.join(font_dir, "PatrickHand-Regular.ttf")

    if not os.path.exists(font_path):
        logger.info("[pdf_annotator] Downloading Patrick Hand font...")
        try:
            urllib.request.urlretrieve(FONT_URL, font_path)
            logger.info("[pdf_annotator] Font downloaded to: %s", font_path)
        except Exception as e:
            logger.error("[pdf_annotator] Failed to download font: %s", e)
            return None

    try:
        font = fitz.Font(fontfile=font_path)
        return font
    except Exception as e:
        logger.error("[pdf_annotator] Failed to load font: %s", e)
        return None


def hex_to_rgb(color_name: str) -> tuple:
    """Convert color name to RGB tuple (0-1 range)."""
    colors = {
        "red": (1, 0, 0),
        "blue": (0, 0, 1),
        "green": (0, 0.5, 0),
        "black": (0, 0, 0),
        "white": (1, 1, 1),
        "yellow": (1, 1, 0),
        "orange": (1, 0.5, 0),
    }
    return colors.get(color_name.lower(), (1, 0, 0))  # default to red


def draw_score_circle(page, x: float, y: float, score_text: str, color: tuple, font, radius: float = 20):
    """
    Draw a score inside a circle.

    Args:
        page: PyMuPDF page object
        x: X coordinate of circle center
        y: Y coordinate of circle center
        score_text: The score text to display (e.g., "8", "4.5")
        color: RGB tuple for the circle and text color
        font: Font object to use for text
        radius: Radius of the circle in points (default 20)
    """
    # Draw the circle outline
    center = fitz.Point(x, y)
    circle_rect = fitz.Rect(x - radius, y - radius, x + radius, y + radius)

    # Draw circle border
    shape = page.new_shape()
    shape.draw_circle(center, radius)
    shape.finish(color=color, width=2, fill=None)  # Outline only, no fill
    shape.commit()

    # Calculate font size based on text length and circle radius
    text_len = len(score_text)
    if text_len <= 2:
        font_size = radius * 1.2
    elif text_len <= 4:
        font_size = radius * 0.9
    else:
        font_size = radius * 0.7

    # Center the text inside the circle
    text_width = len(score_text) * font_size * 0.5
    text_x = x - text_width / 2
    text_y = y + font_size / 3  # Adjust for vertical centering

    # Draw the score text
    tw = fitz.TextWriter(page.rect)
    if font:
        tw.append((text_x, text_y), score_text, fontsize=font_size, font=font)
    else:
        tw.append((text_x, text_y), score_text, fontsize=font_size)
    tw.write_text(page, color=color)


def draw_tick_mark(page, x: float, y: float, color: tuple = (1, 0, 0), size: float = 15):
    """
    Draw a tick/check mark at the specified position.

    Args:
        page: PyMuPDF page object
        x: X coordinate of tick start
        y: Y coordinate of tick start
        color: RGB tuple for the tick color (default red)
        size: Size of the tick mark in points (default 15)
    """
    shape = page.new_shape()

    # Draw a proper checkmark (✓) shape
    # The checkmark has a short downward stroke on the left, then a longer upward stroke to the right

    # Starting point (top-left of the short stroke)
    p1 = fitz.Point(x, y)

    # Bottom point of the checkmark (the vertex)
    p2 = fitz.Point(x + size * 0.35, y + size * 0.6)

    # End point (top-right, higher than start)
    p3 = fitz.Point(x + size, y - size * 0.4)

    # Draw the checkmark as a polyline
    shape.draw_polyline([p1, p2, p3])
    shape.finish(color=color, width=2.5, lineCap=1, lineJoin=1, closePath=False)
    shape.commit()


def add_random_ticks_to_page(page, num_ticks: int = 3, color: tuple = (1, 0, 0)):
    """
    Add random tick marks to a page, avoiding margins.
    Places one tick in each vertical third of the page with different y-coordinates.

    Args:
        page: PyMuPDF page object
        num_ticks: Number of tick marks to add (default 3)
        color: RGB tuple for tick color (default red)
    """
    rect = page.rect

    # Define safe zone (avoid top 10%, bottom 10%, left 10%, right 35%)
    safe_left = rect.width * 0.10
    safe_right = rect.width * 0.65
    safe_top = rect.height * 0.10
    safe_bottom = rect.height * 0.90

    # Divide the page into 3 vertical zones (top 1/3, middle 1/3, bottom 1/3)
    zone_height = (safe_bottom - safe_top) / 3

    # Track used y-coordinates to ensure they don't match
    used_y_coords = []

    for zone_index in range(3):
        # Calculate y range for this zone
        zone_top = safe_top + (zone_index * zone_height)
        zone_bottom = zone_top + zone_height

        # Generate random x position within safe zone
        x = random.uniform(safe_left, safe_right)

        # Generate y position within this zone, ensuring it doesn't match previous ones
        max_attempts = 10
        for _ in range(max_attempts):
            y = random.uniform(zone_top, zone_bottom)
            # Check if y is at least 20 points away from all previous y coords
            is_unique = all(abs(y - prev_y) > 20 for prev_y in used_y_coords)
            if is_unique:
                break

        used_y_coords.append(y)

        # Fixed size of 26 points
        size = 26

        draw_tick_mark(page, x, y, color, size)


def add_margins(doc, right_margin_inches: float = 2.5, bottom_margin_inches: float = 1.0):
    """
    Add right and bottom margins to all pages in the document.
    Creates new pages with expanded dimensions and copies original content.

    Args:
        doc: PyMuPDF document object
        right_margin_inches: Right margin width in inches (default 2.5)
        bottom_margin_inches: Bottom margin height in inches (default 1.0)

    Returns:
        Tuple of (right_margin_pts, bottom_margin_pts)
    """
    right_margin_pts = right_margin_inches * 72  # 1 inch = 72 points
    bottom_margin_pts = bottom_margin_inches * 72

    # Create a new document to hold the modified pages
    new_doc = fitz.open()

    for page_idx in range(len(doc)):
        old_page = doc[page_idx]
        old_rect = old_page.rect
        old_width = old_rect.width
        old_height = old_rect.height

        new_width = old_width + right_margin_pts
        new_height = old_height + bottom_margin_pts

        # Create a new blank page with the expanded dimensions
        new_page = new_doc.new_page(width=new_width, height=new_height)

        # Define where to place the original content on the new page
        # Place it at top-left, leaving bottom margin empty
        # The clip rect is the original page area
        # The target rect is where to draw it on the new page (at top, leaving bottom margin)
        target_rect = fitz.Rect(0, 0, old_width, old_height)

        # Copy the original page content to the new page at the top
        new_page.show_pdf_page(target_rect, doc, page_idx)

    # Replace original doc pages with new ones
    # First, delete all pages from original doc
    while len(doc) > 0:
        doc.delete_page(0)

    # Insert pages from new_doc into original doc
    doc.insert_pdf(new_doc)

    new_doc.close()

    return right_margin_pts, bottom_margin_pts


def download_pdf(pdf_url: str, output_dir: str) -> str:
    """
    Download a PDF from a URL.

    Args:
        pdf_url: URL of the PDF to download
        output_dir: Directory to save the downloaded PDF

    Returns:
        Path to the downloaded PDF file
    """
    os.makedirs(output_dir, exist_ok=True)

    # Generate unique filename
    file_id = str(uuid.uuid4())
    pdf_path = os.path.join(output_dir, f"{file_id}_input.pdf")

    # Download with SSL verification disabled for compatibility
    response = requests.get(pdf_url, verify=False, timeout=60)
    response.raise_for_status()

    with open(pdf_path, 'wb') as f:
        f.write(response.content)

    return pdf_path


def add_annotations_to_pdf(pdf_path: str, annotations: dict, output_path: str, add_margin: bool = True) -> str:
    """
    Add text annotations to PDF based on annotation data.

    Args:
        pdf_path: Path to the source PDF
        annotations: Dictionary with page numbers as keys and list of annotations as values
                    Format: {
                        "1": [{"x": 450, "y": 100, "text": "Comment", "color": "red", "width": 200, "height": 100}],
                        "2": [...]
                    }
        output_path: Path for the output annotated PDF
        add_margin: Whether to add 2.5 inch right margin (default True)

    Returns:
        Path to the annotated PDF
    """
    # Open PDF
    doc = fitz.open(pdf_path)

    # Get page dimensions
    first_page = doc[0]
    page_height = first_page.rect.height
    original_width = first_page.rect.width

    # Add right and bottom margins if requested
    if add_margin:
        add_margins(doc, right_margin_inches=2.5, bottom_margin_inches=1.0)

    # Add random red tick marks to each page (3 ticks per page)
    for page_num in range(len(doc)):
        page = doc[page_num]
        add_random_ticks_to_page(page, num_ticks=3, color=(1, 0, 0))  # Red ticks

    # Load Patrick Hand font
    patrick_hand_font = get_patrick_hand_font()

    for page_num_str, page_annotations in annotations.items():
        page_num = int(page_num_str) - 1  # Convert to 0-based index

        if page_num < 0 or page_num >= len(doc):
            logger.warning("[pdf_annotator] Page %s out of range, skipping", page_num_str)
            continue

        page = doc[page_num]

        for ann in page_annotations:
            x = ann.get("x", 0)
            y = ann.get("y", 0)
            text = ann.get("text", "")
            color = ann.get("color", "red")
            font_size = FONT_SIZE_OVERRIDE
            width = ann.get("width", 200)
            height = ann.get("height", 100)
            ann_type = ann.get("type", "text")  # "text" or "score"
            radius = ann.get("radius", 20)  # For score circles

            logger.debug("[pdf_annotator] Page %s - Annotation type: %s, x: %s, y: %s",
                        page_num_str, ann_type, x, y)

            # Get RGB color
            rgb = hex_to_rgb(color)

            try:
                # Check if this is a score annotation (draw in circle)
                if ann_type == "score":
                    draw_score_circle(page, x, y, text, rgb, patrick_hand_font, radius)
                    continue

                # Check if this is a summary annotation (place in bottom margin)
                if ann_type == "summary":
                    # Get page dimensions (page already has margins added)
                    page_rect = page.rect
                    # Bottom margin is 1 inch = 72 points
                    # The original content ends at (page_height - bottom_margin)
                    # We want to place summary in the bottom margin area
                    bottom_margin_pts = 72  # 1 inch

                    # Summary should start in the bottom margin area
                    # Original content area ends at: page_height - bottom_margin_pts
                    original_content_end = page_rect.height - bottom_margin_pts

                    # Place summary 15 points into the bottom margin
                    summary_y = original_content_end + 15
                    summary_x = 50  # Left padding
                    summary_width = page_rect.width - 100  # Leave some padding on both sides

                    logger.debug("[pdf_annotator] Page %s - Summary placement: x=%s, y=%s, page_height=%s",
                                page_num_str, summary_x, summary_y, page_rect.height)

                    # Create text writer for summary
                    tw = fitz.TextWriter(page_rect)

                    # Use smaller font for summary
                    summary_font_size = 14

                    # Word wrap the summary text
                    char_width = summary_font_size * 0.5
                    chars_per_line = int(summary_width / char_width)

                    words = text.split()
                    lines = []
                    current_line = ""

                    for word in words:
                        test_line = current_line + (" " if current_line else "") + word
                        if len(test_line) <= chars_per_line:
                            current_line = test_line
                        else:
                            if current_line:
                                lines.append(current_line)
                            current_line = word

                    if current_line:
                        lines.append(current_line)

                    # Draw summary lines in bottom margin
                    line_height = summary_font_size * 1.3
                    current_y = summary_y + summary_font_size

                    for line in lines:
                        if patrick_hand_font:
                            tw.append(
                                (summary_x, current_y),
                                line,
                                fontsize=summary_font_size,
                                font=patrick_hand_font,
                            )
                        else:
                            tw.append(
                                (summary_x, current_y),
                                line,
                                fontsize=summary_font_size,
                            )
                        current_y += line_height

                    # Write summary with the specified color
                    tw.write_text(page, color=rgb)
                    continue

                # Regular text annotation
                # Create a text writer
                tw = fitz.TextWriter(page.rect)

                # Apply 0.3 inch (22 points) left offset for text in right margin
                x_offset = 22  # 0.3 inch = 0.3 * 72 ≈ 22 points
                adjusted_x = x - x_offset

                # Calculate characters per line for word wrapping
                char_width = font_size * 0.55
                chars_per_line = int(width / char_width)

                # Word wrap the text
                words = text.split()
                lines = []
                current_line = ""

                for word in words:
                    test_line = current_line + (" " if current_line else "") + word
                    if len(test_line) <= chars_per_line:
                        current_line = test_line
                    else:
                        if current_line:
                            lines.append(current_line)
                        current_line = word

                if current_line:
                    lines.append(current_line)

                # Draw each line
                line_height = font_size * 1.3
                current_y = y + font_size

                for line in lines:
                    if current_y + line_height > y + height:
                        break

                    if patrick_hand_font:
                        tw.append(
                            (adjusted_x, current_y),
                            line,
                            fontsize=font_size,
                            font=patrick_hand_font,
                        )
                    else:
                        tw.append(
                            (adjusted_x, current_y),
                            line,
                            fontsize=font_size,
                        )

                    current_y += line_height

                # Write to page with color
                tw.write_text(page, color=rgb)

            except Exception as e:
                logger.error("[pdf_annotator] Error adding annotation on page %s: %s", page_num_str, e)
                # Fallback: use insert_text
                try:
                    page.insert_text(
                        (x, y + font_size),
                        text[:50],
                        fontsize=font_size,
                        color=rgb,
                    )
                except Exception as e2:
                    logger.error("[pdf_annotator] Fallback also failed: %s", e2)

    # Save the annotated PDF
    doc.save(output_path)
    doc.close()

    return output_path


def process_pdf_with_annotations(pdf_url: str, annotations: dict, output_dir: str = None, add_margin: bool = True) -> dict:
    """
    Main function to download PDF and add annotations.

    Args:
        pdf_url: URL of the PDF to annotate
        annotations: Annotation data dictionary
        output_dir: Directory for output files (default: /app/output or ./output)
        add_margin: Whether to add right margin

    Returns:
        Dictionary with status and output file path
    """
    if output_dir is None:
        output_dir = os.environ.get('OUTPUT_DIR', '/app/output')

    os.makedirs(output_dir, exist_ok=True)

    try:
        # Generate unique ID for this job
        job_id = str(uuid.uuid4())
        logger.info("[pdf_annotator] Job ID: %s", job_id)

        # Download the PDF
        logger.info("[pdf_annotator] Downloading PDF from: %s", pdf_url)
        input_pdf_path = download_pdf(pdf_url, output_dir)
        logger.info("[pdf_annotator] PDF downloaded to: %s", input_pdf_path)

        # Generate output path
        output_pdf_path = os.path.join(output_dir, f"{job_id}_annotated.pdf")

        # Add annotations
        logger.info("[pdf_annotator] Adding annotations to PDF...")
        logger.info("[pdf_annotator] Number of pages with annotations: %d", len(annotations))
        add_annotations_to_pdf(input_pdf_path, annotations, output_pdf_path, add_margin)
        logger.info("[pdf_annotator] Annotated PDF saved to: %s", output_pdf_path)

        # Clean up input file
        try:
            os.remove(input_pdf_path)
            logger.debug("[pdf_annotator] Cleaned up input file")
        except:
            pass

        return {
            'status': 'success',
            'job_id': job_id,
            'output_path': output_pdf_path,
            'message': 'PDF annotated successfully'
        }

    except requests.exceptions.RequestException as e:
        logger.error("[pdf_annotator] Failed to download PDF: %s", str(e))
        return {
            'status': 'error',
            'message': f'Failed to download PDF: {str(e)}'
        }
    except Exception as e:
        logger.error("[pdf_annotator] Failed to process PDF: %s", str(e))
        return {
            'status': 'error',
            'message': f'Failed to process PDF: {str(e)}'
        }

