"""
Base module for document processing and evaluation.
Provides two main functions:
1. extract_text - OCR and text cleaning using Google Vertex AI
2. evaluate_text_assistant_ai - Evaluate student answers using OpenAI Assistant API
"""

import base64
import json
import os
import time
import requests
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple

import fitz  # PyMuPDF


# A4 dimensions in points (72 points = 1 inch)
A4_WIDTH_PT = 595.0   # 210mm
A4_HEIGHT_PT = 842.0  # 297mm


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


VERTEXAI_API_KEY=""
OPENAI_API_KEY=''
YOUR_PROJECT_ID = ""
YOUR_ASSISTANT_ID = ""

class DocumentProcessor:
    """
    Document processor class that handles OCR extraction via Vertex AI
    and text evaluation via OpenAI Assistant API.
    """

    def __init__(
        self,
        # Vertex AI settings
        vertex_api_key: str = VERTEXAI_API_KEY,
        vertex_project_id: str = YOUR_PROJECT_ID,
        vertex_location: str = "us-central1",
        vertex_model_name: str = "gemini-2.5-pro",
        # OpenAI settings
        openai_api_key: str = OPENAI_API_KEY,
        openai_assistant_id: str = YOUR_ASSISTANT_ID,
    ):
        """
        Initialize the DocumentProcessor with API credentials.

        Args:
            vertex_api_key: Google Cloud API key for Vertex AI
            vertex_project_id: Google Cloud project ID
            vertex_location: Vertex AI location (default: us-central1)
            vertex_model_name: Model name for Vertex AI (default: gemini-2.0-flash)
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
        TOLERANCE = 1.0    # Allow 1 point tolerance for floating point comparison

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
                print(f"Page {page_num + 1}: Already A4 ({page_width:.1f} x {page_height:.1f})")
                # Render page to image
                zoom = dpi / 72  # 72 is the default PDF resolution
                mat = fitz.Matrix(zoom, zoom)
                pix = src_page.get_pixmap(matrix=mat)

                # Store metadata - no transformation needed
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
                scale = min(scale_x, scale_y)  # Use smaller scale to fit entirely

                # Calculate new dimensions after scaling
                new_width = page_width * scale
                new_height = page_height * scale

                # Calculate position to center the content (padding on all sides)
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

                print(f"Page {page_num + 1}: Converted to A4 ({page_width:.1f} x {page_height:.1f} -> {A4_WIDTH_PT} x {A4_HEIGHT_PT})")

                # Render the A4 page to image
                zoom = dpi / 72
                mat = fitz.Matrix(zoom, zoom)
                pix = new_page.get_pixmap(matrix=mat)

                # Store metadata with transformation info
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

            print(f"Page {page_num + 1}: Converted to image ({pix.width} x {pix.height} pixels)")

        src_doc.close()

        return images_base64, pages_metadata

    def normalized_to_pdf_coords(
        self,
        normalized_coords: List[float],
        page_metadata: PageMetadata
    ) -> List[float]:
        """
        Convert normalized coordinates (0-1) from OCR to original PDF coordinates in points.

        This handles:
        1. Converting from normalized (0-1) to image pixels
        2. Removing A4 padding offset (if page was converted)
        3. Reversing the scale factor (if page was scaled)
        4. Converting to PDF points (origin top-left, same as image)

        Args:
            normalized_coords: [x1, y1, x2, y2] normalized coordinates (0-1 range)
            page_metadata: PageMetadata object for the page

        Returns:
            [x1, y1, x2, y2] coordinates in PDF points relative to original page
        """
        x1_norm, y1_norm, x2_norm, y2_norm = normalized_coords

        # Step 1: Convert normalized to A4 image pixels
        x1_px = x1_norm * page_metadata.image_width_px
        y1_px = y1_norm * page_metadata.image_height_px
        x2_px = x2_norm * page_metadata.image_width_px
        y2_px = y2_norm * page_metadata.image_height_px

        # Step 2: Convert pixels to A4 points (72 DPI)
        # pdf_coord = image_coord × (72 / DPI)
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

            # Step 4: Reverse the scale factor to get original coordinates
            x1_pt = x1_scaled_pt / page_metadata.scale
            y1_pt = y1_scaled_pt / page_metadata.scale
            x2_pt = x2_scaled_pt / page_metadata.scale
            y2_pt = y2_scaled_pt / page_metadata.scale
        else:
            # No conversion was done, coordinates are already correct
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

        Args:
            ocr_result: Parsed OCR JSON result with normalized coordinates
            pages_metadata: List of PageMetadata objects for each page

        Returns:
            OCR result with coordinates converted to PDF points
        """
        # Create metadata lookup by page number
        metadata_by_page = {m.page_number: m for m in pages_metadata}

        if "Pages" not in ocr_result:
            return ocr_result

        for page in ocr_result["Pages"]:
            page_num = page.get("Page_Number", 1)
            metadata = metadata_by_page.get(page_num)

            if not metadata:
                print(f"Warning: No metadata found for page {page_num}")
                continue

            for block in page.get("Blocks", []):
                for line in block.get("Lines", []):
                    if "Coordinates" in line and len(line["Coordinates"]) == 4:
                        normalized_coords = line["Coordinates"]
                        pdf_coords = self.normalized_to_pdf_coords(normalized_coords, metadata)
                        line["Coordinates"] = pdf_coords
                        line["Coordinates_Normalized"] = normalized_coords  # Keep original for reference

        return ocr_result

    def extract_text(self, file_path: str, convert_coords: bool = True) -> Tuple[str, List[PageMetadata]]:
        """
        Extract and clean text from a PDF using Google Vertex AI.

        This function:
        1. Converts PDF pages to A4 images
        2. Sends images to Vertex AI with a specialized prompt
        3. Returns cleaned, corrected text with coordinate mapping
        4. Optionally converts normalized coordinates to PDF coordinates

        Args:
            file_path: Path to the PDF file to process
            convert_coords: Whether to convert normalized coords to PDF coords (default: True)

        Returns:
            Tuple containing:
                - JSON string containing cleaned text with coordinates
                - List of PageMetadata objects for coordinate conversion

        Raises:
            RuntimeError: If Vertex AI request fails
            FileNotFoundError: If the PDF file doesn't exist
        """
        # Read the prompt from external file
        prompt_file_path = os.path.join(os.path.dirname(__file__), "gemini-prompt.txt")
        with open(prompt_file_path, "r") as f:
            prompt = f.read()

        # Convert PDF pages to A4 images and get metadata
        print("Converting PDF pages to A4 images...")
        images_base64, pages_metadata = self._convert_pdf_to_images(file_path)
        print(f"Conversion complete. {len(images_base64)} page(s) converted to images.")
        print("Sending to Vertex AI...")

        # Build the Vertex AI endpoint URL
        endpoint = (
            f"https://{self.vertex_location}-aiplatform.googleapis.com/v1/"
            f"projects/{self.vertex_project_id}/locations/{self.vertex_location}/"
            f"publishers/google/models/{self.vertex_model_name}:generateContent"
            f"?key={self.vertex_api_key}"
        )

        # Build the parts list with prompt and all images
        parts = [{"text": prompt}]
        for i, img_base64 in enumerate(images_base64):
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

        # Make the API request (10 min timeout for large documents)
        response = requests.post(endpoint, json=payload, timeout=600)

        if not response.ok:
            raise RuntimeError(f"Vertex AI failed: {response.text}")

        data = response.json()

        # Extract the text from the response
        try:
            ocr_text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            return json.dumps(data, indent=2), pages_metadata

        # If coordinate conversion is enabled, convert normalized coords to PDF coords
        if convert_coords:
            try:
                ocr_result = json.loads(ocr_text)
                ocr_result = self.convert_ocr_result_coords(ocr_result, pages_metadata)
                return json.dumps(ocr_result, indent=2), pages_metadata
            except json.JSONDecodeError:
                print("Warning: Could not parse OCR result for coordinate conversion")
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

        This function:
        1. Creates a thread with the student and model answers
        2. Runs the assistant to evaluate
        3. Polls for completion
        4. Returns the evaluation result

        Args:
            student_text: OCR extracted text from student's answer
            student_coordinates: OCR coordinates from student's answer
            model_answer: OCR extracted text from model answer

        Returns:
            Dictionary containing evaluation results, or dict with 'raw' and 'parsed' keys on JSON parse error

        Raises:
            RuntimeError: If any API call fails or run doesn't complete
        """
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

        # 1. Create the thread
        thread_response = requests.post(
            "https://api.openai.com/v1/threads",
            headers=headers,
            json={
                "messages": [
                    {"role": "user", "content": user_prompt},
                ],
            },
        )

        if not thread_response.ok:
            raise RuntimeError(f"Thread creation failed: {thread_response.text}")

        thread_data = thread_response.json()
        thread_id = thread_data.get("id")

        # 2. Create the run
        run_response = requests.post(
            f"https://api.openai.com/v1/threads/{thread_id}/runs",
            headers=headers,
            json={
                "assistant_id": self.openai_assistant_id,
                "response_format": {"type": "json_object"},
            },
        )

        if not run_response.ok:
            raise RuntimeError(f"Run creation failed: {run_response.text}")

        run_data = run_response.json()
        run_id = run_data.get("id")

        if not run_id:
            raise RuntimeError(f"Run ID missing in response: {json.dumps(run_data)}")

        # 3. Poll for completion
        status = None
        while status not in ["completed", "failed", "cancelled"]:
            time.sleep(2)

            status_response = requests.get(
                f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}",
                headers=headers,
            )

            if not status_response.ok:
                raise RuntimeError(f"Failed to get run status: {status_response.text}")

            status_data = status_response.json()
            status = status_data.get("status")
            print(f"Assistant run status: {status}")

        if status != "completed":
            raise RuntimeError(f"Run did not complete successfully (status: {status})")

        # 4. Get messages
        messages_response = requests.get(
            f"https://api.openai.com/v1/threads/{thread_id}/messages",
            headers=headers,
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
            print(f"Warning: Assistant output was not valid JSON - {e}")
            print(f"Raw output: {assistant_text}")
            return {
                "raw": assistant_text,
                "parsed": None,
            }

    def _clean_json_response(self, text: str) -> str:
        """
        Clean JSON response by removing markdown code blocks.

        Args:
            text: Raw response text that may contain ```json ... ``` blocks

        Returns:
            Cleaned JSON string
        """
        text = text.strip()

        # Remove ```json at the start
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]

        # Remove ``` at the end
        if text.endswith("```"):
            text = text[:-3]

        return text.strip()


# Convenience functions for direct use without class instantiation

def extract_text(
    file_path: str,
    api_key: str,
    project_id: str,
    location: str = "us-central1",
    model_name: str = "gemini-2.0-flash",
) -> Tuple[str, List[PageMetadata]]:
    """
    Extract and clean text from a PDF using Google Vertex AI.

    Args:
        file_path: Path to the PDF file
        api_key: Google Cloud API key
        project_id: Google Cloud project ID
        location: Vertex AI location (default: us-central1)
        model_name: Model name (default: gemini-2.0-flash)

    Returns:
        Tuple of (JSON string with cleaned text and coordinates, List of PageMetadata)
    """
    processor = DocumentProcessor(
        vertex_api_key=api_key,
        vertex_project_id=project_id,
        vertex_location=location,
        vertex_model_name=model_name,
    )
    return processor.extract_text(file_path)


def evaluate_text_assistant_ai(
    student_text: str,
    student_coordinates: str,
    model_answer: str,
    api_key: str,
    assistant_id: str,
) -> Dict[str, Any]:
    """
    Evaluate student answer against model answer using OpenAI Assistant API.

    Args:
        student_text: OCR extracted text from student's answer
        student_coordinates: OCR coordinates from student's answer
        model_answer: OCR extracted text from model answer
        api_key: OpenAI API key
        assistant_id: OpenAI Assistant ID

    Returns:
        Dictionary with evaluation results
    """
    processor = DocumentProcessor(
        openai_api_key=api_key,
        openai_assistant_id=assistant_id,
    )
    return processor.evaluate_text_assistant_ai(
        student_text, student_coordinates, model_answer
    )


if __name__ == "__main__":
    # Example usage - replace with your actual credentials and file paths

    # Example 1: Extract text from PDF using Vertex AI
    # result = extract_text(
    #     file_path="path/to/your/document.pdf",
    #     api_key="YOUR_VERTEX_API_KEY",
    #     project_id="YOUR_PROJECT_ID",
    #     location="us-central1",
    #     model_name="gemini-2.0-flash",
    # )
    # print(result)

    # Example 2: Evaluate student answer using OpenAI Assistant
    # evaluation = evaluate_text_assistant_ai(
    #     student_text="Student's answer text here...",
    #     student_coordinates='{"coordinates": [...]}',
    #     model_answer="Model answer text here...",
    #     api_key="YOUR_OPENAI_API_KEY",
    #     assistant_id="YOUR_ASSISTANT_ID",
    # )
    # print(evaluation)

    print("DocumentProcessor module loaded successfully.")
    print("Use DocumentProcessor class or convenience functions:")
    print("  - extract_text() for Vertex AI OCR")
    print("  - evaluate_text_assistant_ai() for OpenAI evaluation")

