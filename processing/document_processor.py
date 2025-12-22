"""
Document Processor - OCR and text evaluation using Vertex AI and OpenAI.

Provides two main functions:
1. extract_text - OCR and text cleaning using Google Vertex AI / Gemini
2. evaluate_text_assistant_ai - Evaluate student answers using OpenAI Assistant API
"""

import base64
import json
import logging
import os
import re
import time
import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple

import fitz  # PyMuPDF

# Suppress SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Get logger for this module
logger = logging.getLogger(__name__)


def sanitize_json_string(json_str: str) -> str:
    """
    Sanitize JSON string to fix common Gemini hallucination errors.

    Fixes issues like:
    - "0 astounding" instead of "0.12"
    - Random words inserted in number arrays
    - Hindi/Devanagari text inserted in coordinate arrays
    - Text with special characters like parentheses in coordinates
    """
    # Pattern 1: Replace "number word" patterns with just "number.0"
    # e.g., "0 astounding" -> "0.0"
    json_str = re.sub(r'(\d+)\s+[a-zA-Z]+\s*,', r'\1.0,', json_str)

    # Pattern 2: Remove standalone ASCII words in arrays
    # e.g., [0.12, word, 0.34] -> [0.12, 0.0, 0.34]
    json_str = re.sub(r',\s*[a-zA-Z_]+\s*,', ', 0.0,', json_str)

    # Pattern 3: Fix coordinate arrays with text/Hindi content inserted
    # This handles cases like:
    #   "Coordinates": [0.22, 0.627, \n विशेषाधिकार (Art 29, Art 30)", 0.862, 0.712]
    # We need to find and fix malformed coordinate arrays

    # Find all "Coordinates": [...] or "coordinates": [...] blocks and validate them
    def fix_coordinates_array(match):
        full_match = match.group(0)
        key = match.group(1)  # "Coordinates" or "coordinates"
        content = match.group(2)  # The array content

        # Split by comma and filter to keep only valid numbers
        parts = content.split(',')
        cleaned_parts = []

        for part in parts:
            part = part.strip()
            # Check if it's a valid number (integer or decimal)
            if re.match(r'^-?\d+\.?\d*$', part):
                cleaned_parts.append(part)
            elif re.match(r'^-?\d+\.\d*$', part.split()[0] if part.split() else ''):
                # Handle "0.627\n text" - take just the number
                num_match = re.match(r'^(-?\d+\.?\d*)', part)
                if num_match:
                    cleaned_parts.append(num_match.group(1))
                else:
                    cleaned_parts.append('0.0')
            else:
                # Try to extract any number from the part
                num_match = re.search(r'(-?\d+\.?\d*)', part)
                if num_match and len(num_match.group(1)) > 0:
                    cleaned_parts.append(num_match.group(1))
                # Skip non-numeric parts entirely

        # Ensure we have exactly 4 coordinates, pad with 0.0 if needed
        while len(cleaned_parts) < 4:
            cleaned_parts.append('0.0')
        cleaned_parts = cleaned_parts[:4]  # Take only first 4

        return f'"{key}": [{", ".join(cleaned_parts)}]'

    # Apply coordinate array fix - match multiline coordinate arrays
    json_str = re.sub(
        r'"(Coordinates|coordinates)":\s*\[\s*([^\]]+)\]',
        fix_coordinates_array,
        json_str,
        flags=re.MULTILINE | re.DOTALL
    )

    return json_str


def safe_json_loads(json_str: str) -> dict:
    """
    Safely parse JSON string with sanitization fallback.

    Args:
        json_str: JSON string to parse

    Returns:
        Parsed dictionary

    Raises:
        json.JSONDecodeError if parsing fails even after sanitization
    """
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning("Initial JSON parse failed: %s", e)
        logger.warning("Attempting to sanitize JSON...")

        # Try sanitizing and parsing again
        sanitized = sanitize_json_string(json_str)
        try:
            result = json.loads(sanitized)
            logger.info("Successfully parsed sanitized JSON")
            return result
        except json.JSONDecodeError as e2:
            logger.error("JSON parse failed even after sanitization: %s", e2)
            raise

# A4 dimensions in points (72 points = 1 inch)
A4_WIDTH_PT = 595.0   # 210mm
A4_HEIGHT_PT = 842.0  # 297mm


def create_retry_session(retries=5, backoff_factor=2, status_forcelist=(500, 502, 503, 504)):
    """Create a requests session with retry logic."""
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session


@dataclass
class PageMetadata:
    """
    Stores metadata about a page's original dimensions and transformation.
    Used for converting normalized coordinates back to original PDF coordinates.
    """
    page_number: int  # 1-indexed
    original_width_pt: float  # Original page width in points
    original_height_pt: float  # Original page height in points
    was_converted: bool  # Whether the page was converted to A4
    scale: float  # Scale factor applied (1.0 if no conversion)
    x_offset_pt: float  # X offset in points (padding left)
    y_offset_pt: float  # Y offset in points (padding top)
    image_width_px: int  # Final image width in pixels
    image_height_px: int  # Final image height in pixels
    dpi: int  # DPI used for image conversion


class DocumentProcessor:
    """
    Document processor class that handles OCR extraction via Vertex AI
    and text evaluation via OpenAI Assistant API.
    """

    def __init__(
        self,
        vertex_api_key: str,
        vertex_project_id: str,
        vertex_location: str = "us-central1",
        vertex_model_name: str = "gemini-2.5-pro",
        openai_api_key: str = None,
        openai_assistant_id: str = None,
    ):
        """
        Initialize the DocumentProcessor with API credentials.

        Args:
            vertex_api_key: Google Cloud API key for Vertex AI
            vertex_project_id: Google Cloud project ID
            vertex_location: Vertex AI location (default: us-central1)
            vertex_model_name: Model name for Vertex AI
            openai_api_key: OpenAI API key
            openai_assistant_id: OpenAI Assistant ID for evaluation
        """
        # Vertex AI configuration
        self.vertex_api_key = vertex_api_key
        self.vertex_project_id = vertex_project_id
        self.vertex_location = vertex_location
        self.vertex_model_name = vertex_model_name

        # OpenAI configuration
        self.openai_api_key = openai_api_key
        self.openai_assistant_id = openai_assistant_id

    def _convert_pdf_to_images(self, file_path: str, dpi: int = 200) -> Tuple[List[str], List[PageMetadata]]:
        """
        Convert each page of the PDF to A4 size (if needed) and then to an image.
        Also stores metadata about original dimensions and transformations for coordinate conversion.

        Args:
            file_path: Path to the input PDF file
            dpi: Resolution for image conversion (default: 200)

        Returns:
            Tuple containing:
                - List of base64 encoded PNG images, one per page
                - List of PageMetadata objects with transformation info
        """
        TOLERANCE = 1.0  # Allow 1 point tolerance for floating point comparison

        # Open the source PDF
        src_doc = fitz.open(file_path)

        # Lists to store results
        images_base64 = []
        pages_metadata = []

        for page_num in range(len(src_doc)):
            src_page = src_doc[page_num]
            page_width = src_page.rect.width
            page_height = src_page.rect.height

            # Check if page is already A4 (within tolerance)
            is_a4 = (
                abs(page_width - A4_WIDTH_PT) <= TOLERANCE and
                abs(page_height - A4_HEIGHT_PT) <= TOLERANCE
            )

            if is_a4:
                # Page is already A4, render directly
                logger.debug("Page %d: Already A4 (%.1f x %.1f)", page_num + 1, page_width, page_height)
                zoom = dpi / 72
                mat = fitz.Matrix(zoom, zoom)
                pix = src_page.get_pixmap(matrix=mat)

                metadata = PageMetadata(
                    page_number=page_num + 1,
                    original_width_pt=page_width,
                    original_height_pt=page_height,
                    was_converted=False,
                    scale=1.0,
                    x_offset_pt=0.0,
                    y_offset_pt=0.0,
                    image_width_px=pix.width,
                    image_height_px=pix.height,
                    dpi=dpi
                )
            else:
                # Create a temporary document with A4 page
                temp_doc = fitz.open()
                new_page = temp_doc.new_page(width=A4_WIDTH_PT, height=A4_HEIGHT_PT)

                # Calculate scaling to fit the content on A4 while maintaining aspect ratio
                scale_x = A4_WIDTH_PT / page_width
                scale_y = A4_HEIGHT_PT / page_height
                scale = min(scale_x, scale_y)

                # Calculate new dimensions after scaling
                new_width = page_width * scale
                new_height = page_height * scale

                # Calculate position to center the content
                x_offset = (A4_WIDTH_PT - new_width) / 2
                y_offset = (A4_HEIGHT_PT - new_height) / 2

                # Define the target rectangle on the new A4 page
                target_rect = fitz.Rect(
                    x_offset,
                    y_offset,
                    x_offset + new_width,
                    y_offset + new_height
                )

                # Copy the source page content to the new page
                new_page.show_pdf_page(target_rect, src_doc, page_num)

                logger.debug("Page %d: Converted to A4 (%.1f x %.1f -> %s x %s)",
                            page_num + 1, page_width, page_height, A4_WIDTH_PT, A4_HEIGHT_PT)

                # Render the A4 page to image
                zoom = dpi / 72
                mat = fitz.Matrix(zoom, zoom)
                pix = new_page.get_pixmap(matrix=mat)

                metadata = PageMetadata(
                    page_number=page_num + 1,
                    original_width_pt=page_width,
                    original_height_pt=page_height,
                    was_converted=True,
                    scale=scale,
                    x_offset_pt=x_offset,
                    y_offset_pt=y_offset,
                    image_width_px=pix.width,
                    image_height_px=pix.height,
                    dpi=dpi
                )

                temp_doc.close()

            # Convert pixmap to PNG bytes and then to base64
            png_bytes = pix.tobytes("png")
            base64_image = base64.b64encode(png_bytes).decode("utf-8")
            images_base64.append(base64_image)
            pages_metadata.append(metadata)

            logger.debug("Page %d: Converted to image (%d x %d pixels)",
                        page_num + 1, pix.width, pix.height)

        src_doc.close()

        return images_base64, pages_metadata

    def normalized_to_pdf_coords(
        self,
        normalized_coords: List[float],
        page_metadata: PageMetadata
    ) -> List[float]:
        """
        Convert normalized coordinates (0-1) from OCR to original PDF coordinates in points.
        """
        x1_norm, y1_norm, x2_norm, y2_norm = normalized_coords

        # Step 1: Convert normalized to A4 image pixels
        x1_px = x1_norm * page_metadata.image_width_px
        y1_px = y1_norm * page_metadata.image_height_px
        x2_px = x2_norm * page_metadata.image_width_px
        y2_px = y2_norm * page_metadata.image_height_px

        # Step 2: Convert pixels to A4 points (72 DPI)
        scale_px_to_pt = 72.0 / page_metadata.dpi
        x1_a4_pt = x1_px * scale_px_to_pt
        y1_a4_pt = y1_px * scale_px_to_pt
        x2_a4_pt = x2_px * scale_px_to_pt
        y2_a4_pt = y2_px * scale_px_to_pt

        if page_metadata.was_converted:
            # Step 3: Remove A4 padding offset
            x1_scaled_pt = x1_a4_pt - page_metadata.x_offset_pt
            y1_scaled_pt = y1_a4_pt - page_metadata.y_offset_pt
            x2_scaled_pt = x2_a4_pt - page_metadata.x_offset_pt
            y2_scaled_pt = y2_a4_pt - page_metadata.y_offset_pt

            # Step 4: Reverse the scale factor
            x1_pt = x1_scaled_pt / page_metadata.scale
            y1_pt = y1_scaled_pt / page_metadata.scale
            x2_pt = x2_scaled_pt / page_metadata.scale
            y2_pt = y2_scaled_pt / page_metadata.scale
        else:
            x1_pt = x1_a4_pt
            y1_pt = y1_a4_pt
            x2_pt = x2_a4_pt
            y2_pt = y2_a4_pt

        # Clamp coordinates to page bounds
        x1_pt = max(0.0, min(x1_pt, page_metadata.original_width_pt))
        y1_pt = max(0.0, min(y1_pt, page_metadata.original_height_pt))
        x2_pt = max(0.0, min(x2_pt, page_metadata.original_width_pt))
        y2_pt = max(0.0, min(y2_pt, page_metadata.original_height_pt))

        return [round(x1_pt, 2), round(y1_pt, 2), round(x2_pt, 2), round(y2_pt, 2)]

    def convert_ocr_result_coords(
        self,
        ocr_result: Dict[str, Any],
        pages_metadata: List[PageMetadata]
    ) -> Dict[str, Any]:
        """
        Convert all normalized coordinates in OCR result to PDF coordinates.
        """
        metadata_by_page = {m.page_number: m for m in pages_metadata}

        if "Pages" not in ocr_result:
            return ocr_result

        for page in ocr_result["Pages"]:
            page_num = page.get("Page_Number", 1)
            metadata = metadata_by_page.get(page_num)

            if not metadata:
                logger.warning("No metadata found for page %d", page_num)
                continue

            for block in page.get("Blocks", []):
                for line in block.get("Lines", []):
                    if "Coordinates" in line and len(line["Coordinates"]) == 4:
                        normalized_coords = line["Coordinates"]
                        pdf_coords = self.normalized_to_pdf_coords(normalized_coords, metadata)
                        line["Coordinates"] = pdf_coords
                        line["Coordinates_Normalized"] = normalized_coords

        return ocr_result

    def extract_text(self, file_path: str, convert_coords: bool = True) -> Tuple[str, List[PageMetadata]]:
        """
        Extract and clean text from a PDF using Google Vertex AI.

        Args:
            file_path: Path to the PDF file to process
            convert_coords: Whether to convert normalized coords to PDF coords

        Returns:
            Tuple containing:
                - JSON string containing cleaned text with coordinates
                - List of PageMetadata objects for coordinate conversion
        """
        # Read the prompt from external file
        prompt_file_path = os.path.join(os.path.dirname(__file__), "gemini-prompt.txt")
        with open(prompt_file_path, "r") as f:
            prompt = f.read()

        # Convert PDF pages to A4 images and get metadata
        logger.info("Converting PDF pages to A4 images...")
        images_base64, pages_metadata = self._convert_pdf_to_images(file_path)
        logger.info("Conversion complete. %d page(s) converted to images.", len(images_base64))
        logger.info("Sending to Vertex AI...")

        # Build the Vertex AI endpoint URL
        endpoint = (
            f"https://{self.vertex_location}-aiplatform.googleapis.com/v1/"
            f"projects/{self.vertex_project_id}/locations/{self.vertex_location}/"
            f"publishers/google/models/{self.vertex_model_name}:generateContent"
            f"?key={self.vertex_api_key}"
        )

        # Build the parts list with prompt and all images
        parts = [{"text": prompt}]
        for img_base64 in images_base64:
            parts.append({
                "inline_data": {
                    "mime_type": "image/png",
                    "data": img_base64,
                }
            })

        # Build the request payload
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": parts,
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 65536,
                "responseMimeType": "application/json",
            },
        }

        # Make the API request with retry logic (10 min timeout for large documents)
        # Using verify=False due to SSL issues in Docker containers
        session = create_retry_session(retries=3, backoff_factor=2)
        response = session.post(endpoint, json=payload, timeout=600, verify=False)

        if not response.ok:
            raise RuntimeError(f"Vertex AI failed: {response.text}")

        data = response.json()

        # Extract the text from the response
        try:
            ocr_text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            return json.dumps(data, indent=2, ensure_ascii=False), pages_metadata

        # If coordinate conversion is enabled, convert normalized coords to PDF coords
        if convert_coords:
            try:
                ocr_result = safe_json_loads(ocr_text)
                ocr_result = self.convert_ocr_result_coords(ocr_result, pages_metadata)
                return json.dumps(ocr_result, indent=2, ensure_ascii=False), pages_metadata
            except json.JSONDecodeError:
                logger.warning("Could not parse OCR result for coordinate conversion")
                return ocr_text, pages_metadata

        return ocr_text, pages_metadata

    def evaluate_text_assistant_ai(
        self,
        student_text: str,
        student_coordinates: str,
        model_answer: str,
    ) -> Dict[str, Any]:
        """
        Evaluate student answer against model answer using OpenAI Assistant API.

        Args:
            student_text: OCR extracted text from student's answer
            student_coordinates: OCR coordinates from student's answer
            model_answer: OCR extracted text from model answer

        Returns:
            Dictionary containing evaluation results
        """
        if not self.openai_api_key or not self.openai_assistant_id:
            raise RuntimeError("OpenAI API key and Assistant ID are required for evaluation")

        # Build the user prompt
        user_prompt = f"""STUDENT ANSWER (OCR extracted):
{student_text}

STUDENT ANSWER (OCR Coordinates):
{student_coordinates}

MODEL ANSWER (OCR extracted):
{model_answer}"""

        # Set up headers for OpenAI API
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "assistants=v2",
        }

        # Create session with retry logic
        session = create_retry_session(retries=3, backoff_factor=2)

        # 1. Create the thread
        thread_response = session.post(
            "https://api.openai.com/v1/threads",
            headers=headers,
            json={
                "messages": [
                    {"role": "user", "content": user_prompt},
                ],
            },
            verify=False,
        )

        if not thread_response.ok:
            raise RuntimeError(f"Thread creation failed: {thread_response.text}")

        thread_data = thread_response.json()
        thread_id = thread_data.get("id")

        # 2. Create the run
        run_response = session.post(
            f"https://api.openai.com/v1/threads/{thread_id}/runs",
            headers=headers,
            json={
                "assistant_id": self.openai_assistant_id,
                "response_format": {"type": "json_object"},
            },
            verify=False,
        )

        if not run_response.ok:
            raise RuntimeError(f"Run creation failed: {run_response.text}")

        run_data = run_response.json()
        run_id = run_data.get("id")

        if not run_id:
            raise RuntimeError(f"Run ID missing in response: {json.dumps(run_data)}")

        # 3. Poll for completion with timeout and retry logic
        status = None
        max_wait_time = 600  # Maximum 10 minutes for OpenAI to complete
        poll_interval = 3   # Check every 3 seconds
        elapsed_time = 0

        while status not in ["completed", "failed", "cancelled", "expired"]:
            if elapsed_time >= max_wait_time:
                logger.warning("OpenAI Assistant timed out after %d seconds", elapsed_time)
                raise RuntimeError(f"OpenAI Assistant timed out after {max_wait_time} seconds")

            time.sleep(poll_interval)
            elapsed_time += poll_interval

            try:
                status_response = session.get(
                    f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                    headers=headers,
                    verify=False,
                    timeout=30,
                )

                if not status_response.ok:
                    logger.warning("Failed to get run status (attempt will retry): %s", status_response.text)
                    continue

                status_data = status_response.json()
                status = status_data.get("status")
                logger.debug("Assistant run status: %s (elapsed: %ds)", status, elapsed_time)

            except requests.exceptions.RequestException as e:
                logger.warning("Request error during polling (will retry): %s", str(e))
                continue

        if status != "completed":
            raise RuntimeError(f"Run did not complete successfully (status: {status})")

        # 4. Get messages
        messages_response = session.get(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers=headers,
            verify=False,
        )

        if not messages_response.ok:
            raise RuntimeError(f"Failed to fetch messages: {messages_response.text}")

        messages_data = messages_response.json()

        # Extract assistant's response
        try:
            assistant_text = messages_data["data"][0]["content"][0]["text"]["value"]
        except (KeyError, IndexError):
            raise RuntimeError("No content found in assistant response.")

        # Clean markdown code blocks if present
        assistant_text = self._clean_json_response(assistant_text)

        # Try to parse JSON output
        try:
            assistant_array = json.loads(assistant_text)
            return assistant_array
        except json.JSONDecodeError as e:
            logger.warning("Assistant output was not valid JSON - %s", e)
            logger.warning("Raw output: %s", assistant_text)
            return {
                "raw": assistant_text,
                "parsed": None,
            }

    def _clean_json_response(self, text: str) -> str:
        """Clean JSON response by removing markdown code blocks."""
        text = text.strip()

        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]

        if text.endswith("```"):
            text = text[:-3]

        return text.strip()

