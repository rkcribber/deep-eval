"""
Document Processing Pipeline

This module provides the full pipeline that:
1. OCR with Vertex AI / Gemini
1b. Insert blank page based on empty_page_detection (NEW)
2. Evaluate with OpenAI Assistant
3. Create annotated PDF
4. Send evaluation result to external API

Equivalent to running: python run.py document.pdf
"""

import json
import os
import requests
import urllib3
import fitz  # PyMuPDF
from typing import Dict, Any, Tuple, Callable

from .document_processor import DocumentProcessor, safe_json_loads
from .annotate_pdf import annotate_pdf_with_comments
from .pdf_annotator import add_margins
from .do_spaces import upload_to_spaces

# Import task logger for structured logging
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logger import get_task_logger, TaskLoggerAdapter

# Suppress SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# External API configuration
EXTERNAL_API_URL = "https://deep-evaluation.theiashub.com/api/mains-copies/update"
EXTERNAL_API_MAX_RETRIES = 3

# A4 page dimensions in points (72 points = 1 inch)
A4_WIDTH = 595   # 8.27 inches
A4_HEIGHT = 842  # 11.69 inches


def insert_blank_page_in_pdf(pdf_path: str, position: int, output_path: str, log) -> str:
    """
    Insert a blank A4 page at the specified position in the PDF.

    Args:
        pdf_path: Path to the input PDF
        position: Page number where to insert (1-based).
                  Position 1 means insert at the beginning.
                  Position 3 means insert after page 2.
        output_path: Path to save the modified PDF
        log: Logger for status messages

    Returns:
        Path to the modified PDF
    """
    log.info("Inserting blank A4 page at position %d", position)

    doc = fitz.open(pdf_path)

    # Convert 1-based position to 0-based index for insertion
    # Position 1 -> insert at index 0 (before first page)
    # Position 3 -> insert at index 2 (after second page, before third)
    insert_index = position - 1

    # Ensure insert_index is within valid range
    if insert_index < 0:
        insert_index = 0
    elif insert_index > len(doc):
        insert_index = len(doc)

    # Insert a new blank A4 page at the specified position
    doc.insert_page(insert_index, width=A4_WIDTH, height=A4_HEIGHT)

    log.info("✅ Blank page inserted at position %d (index %d). Total pages: %d",
             position, insert_index, len(doc))

    # Save the modified PDF
    doc.save(output_path)
    doc.close()

    return output_path


def sanitize_for_json(text: str) -> str:
    """
    Sanitize text for JSON by removing or replacing problematic characters.

    This handles:
    - Control characters (except newline, tab, carriage return)
    - Invalid Unicode characters
    - Characters that might break JSON parsing on some servers
    """
    if not isinstance(text, str):
        return text

    # Remove control characters except \n, \r, \t
    # Control characters are in ranges 0x00-0x1F and 0x7F-0x9F
    cleaned = ""
    for char in text:
        code = ord(char)
        # Keep printable ASCII, newlines, tabs, and valid Unicode
        if code == 0x09 or code == 0x0A or code == 0x0D:  # tab, newline, carriage return
            cleaned += char
        elif code >= 0x20 and code <= 0x7E:  # printable ASCII
            cleaned += char
        elif code >= 0xA0:  # Valid Unicode (non-control)
            cleaned += char
        # else: skip control characters

    return cleaned


def sanitize_evaluation(evaluation: dict) -> dict:
    """
    Recursively sanitize all string values in the evaluation dictionary.
    """
    if isinstance(evaluation, dict):
        return {k: sanitize_evaluation(v) for k, v in evaluation.items()}
    elif isinstance(evaluation, list):
        return [sanitize_evaluation(item) for item in evaluation]
    elif isinstance(evaluation, str):
        return sanitize_for_json(evaluation)
    else:
        return evaluation


def send_evaluation_to_external_api(
    uid: str,
    evaluation: Dict[str, Any],
    log: TaskLoggerAdapter,
    output_dir: str = None
) -> Tuple[bool, str]:
    """
    Send the OpenAI evaluation result to the external API.

    Retries up to 3 times and checks for 200 OK response.

    Args:
        uid: The unique identifier for the mains copy
        evaluation: The OpenAI evaluation result dictionary
        log: Logger with task context
        output_dir: Optional directory to save debug payload

    Returns:
        Tuple of (success: bool, message: str)
    """
    # Sanitize evaluation to remove problematic characters
    evaluation = sanitize_evaluation(evaluation)
    log.info("Sanitized evaluation to remove control characters")

    # Convert evaluation to JSON string with ensure_ascii=True for maximum compatibility
    # This escapes all non-ASCII characters as \uXXXX sequences
    openai_response_str = json.dumps(evaluation, ensure_ascii=True)

    # Build payload with openai_response as a string value
    payload = {
        "uid": str(uid),  # Ensure uid is a string
        "data": {
            "status": "OCR Completed",  # Status to indicate OCR processing is done
            "openai_response": openai_response_str  # String value, not object
        }
    }

    headers = {
        "Content-Type": "application/json"
    }

    # Serialize the full payload to JSON with ensure_ascii=True for maximum compatibility
    payload_json = json.dumps(payload, ensure_ascii=True)
    payload_size = len(payload_json.encode('utf-8'))

    # Save payload to file for debugging (so you can test with curl/Postman)
    if output_dir:
        debug_payload_path = os.path.join(output_dir, f"debug_external_api_payload_{uid}.json")
        try:
            with open(debug_payload_path, 'w') as f:
                f.write(payload_json)
            log.info("Debug payload saved to: %s", debug_payload_path)
        except Exception as e:
            log.warning("Could not save debug payload: %s", e)

    log.info("=" * 60)
    log.info("EXTERNAL API REQUEST DETAILS:")
    log.info("  URL: %s", EXTERNAL_API_URL)
    log.info("  Method: PUT")
    log.info("  Headers: %s", headers)
    log.info("  Payload structure: {uid: '%s', data: {status: 'OCR Completed', openai_response: '<string with %d chars>'}}",
             uid, len(openai_response_str))
    log.info("  Total payload size: %d bytes", payload_size)
    log.info("  Full payload (first 1000 chars): %s", payload_json[:1000])
    if payload_size > 1000:
        log.info("  ... (truncated, full size: %d bytes)", payload_size)
    log.info("=" * 60)

    last_error = None

    for attempt in range(1, EXTERNAL_API_MAX_RETRIES + 1):
        try:
            log.info("Sending PUT request to external API (attempt %d/%d)...",
                    attempt, EXTERNAL_API_MAX_RETRIES)

            # Encode payload as UTF-8 bytes to ensure proper encoding
            payload_bytes = payload_json.encode('utf-8')

            # Use explicit charset in Content-Type header
            request_headers = {
                "Content-Type": "application/json; charset=utf-8"
            }

            response = requests.put(
                EXTERNAL_API_URL,
                data=payload_bytes,  # Send as UTF-8 encoded bytes
                headers=request_headers,
                timeout=60,
                verify=False
            )

            log.info("Response status code: %d", response.status_code)
            log.info("Response headers: %s", dict(response.headers))

            if response.status_code == 200:
                log.info("✅ Successfully sent evaluation to external API (uid: %s)", uid)
                log.info("Response body: %s", response.text[:500] if response.text else "(empty)")
                return True, "Success"
            else:
                last_error = f"HTTP {response.status_code}: {response.text[:500]}"
                log.warning("⚠️ External API returned status %d (attempt %d/%d): %s",
                           response.status_code, attempt, EXTERNAL_API_MAX_RETRIES,
                           response.text[:500])

        except requests.exceptions.Timeout as e:
            last_error = f"Timeout: {str(e)}"
            log.warning("⚠️ External API timeout (attempt %d/%d): %s",
                       attempt, EXTERNAL_API_MAX_RETRIES, str(e))

        except requests.exceptions.RequestException as e:
            last_error = f"Request error: {str(e)}"
            log.warning("⚠️ External API request failed (attempt %d/%d): %s",
                       attempt, EXTERNAL_API_MAX_RETRIES, str(e))

    # All retries exhausted
    log.error("❌ Failed to send evaluation to external API after %d attempts. Last error: %s",
             EXTERNAL_API_MAX_RETRIES, last_error)
    return False, last_error


def trigger_process_api(log: TaskLoggerAdapter) -> Tuple[bool, str]:
    """
    Trigger the process API after successful evaluation update.

    This calls GET http://deep-evaluation.theiashub.com/api/mains-copies/process
    to trigger processing of the updated mains copy.

    Args:
        log: Logger with task context

    Returns:
        Tuple of (success: bool, message: str)
    """
    process_url = "https://deep-evaluation.theiashub.com/api/mains-copies/process"

    log.info("=" * 60)
    log.info("TRIGGERING PROCESS API:")
    log.info("  URL: %s", process_url)
    log.info("  Method: GET")
    log.info("=" * 60)

    try:
        response = requests.get(
            process_url,
            timeout=30,
            verify=False
        )

        log.info("Process API response status: %d", response.status_code)

        if response.status_code == 200:
            log.info("✅ Process API triggered successfully")
            log.info("Response: %s", response.text[:500] if response.text else "(empty)")
            return True, "Success"
        else:
            error_msg = f"HTTP {response.status_code}: {response.text[:500]}"
            log.warning("⚠️ Process API returned status %d: %s",
                       response.status_code, response.text[:500])
            return False, error_msg

    except requests.exceptions.Timeout as e:
        error_msg = f"Timeout: {str(e)}"
        log.warning("⚠️ Process API timeout: %s", str(e))
        return False, error_msg

    except requests.exceptions.RequestException as e:
        error_msg = f"Request error: {str(e)}"
        log.warning("⚠️ Process API request failed: %s", str(e))
        return False, error_msg


def normalize_ocr_data(ocr_data) -> dict:
    """
    Normalize OCR data to ensure it's a dictionary with 'Pages' key.

    Handles cases where Gemini returns:
    - A list containing the result: [{...}]
    - A list of pages directly: [{"Page_Number": 1, ...}, ...]
    - A proper dict: {"Pages": [...]}
    """
    # If it's a list, try to extract the dict
    if isinstance(ocr_data, list):
        if len(ocr_data) == 1 and isinstance(ocr_data[0], dict):
            # Case: [{...}] - list with single dict
            ocr_data = ocr_data[0]
        elif len(ocr_data) > 0 and isinstance(ocr_data[0], dict) and "Page_Number" in ocr_data[0]:
            # Case: [{"Page_Number": 1, ...}, ...] - list of pages directly
            ocr_data = {"Pages": ocr_data}

    # If it still doesn't have "Pages" key, check if it has the structure inside
    if isinstance(ocr_data, dict) and "Pages" not in ocr_data:
        # Maybe the pages are at a different level
        if "Page_Number" in ocr_data:
            ocr_data = {"Pages": [ocr_data]}

    return ocr_data


def extract_text_from_ocr(ocr_data) -> str:
    """Extract plain text from OCR JSON result."""
    # Normalize the data first
    ocr_data = normalize_ocr_data(ocr_data)

    text_parts = []
    for page in ocr_data.get("Pages", []):
        for block in page.get("Blocks", []):
            for line in block.get("Lines", []):
                text = line.get("text", "")
                if text:
                    text_parts.append(text)
    return "\n".join(text_parts)


def validate_evaluation_json(evaluation: dict) -> Tuple[bool, list]:
    """
    Validate that the evaluation JSON has all required fields.

    Returns:
        Tuple of (is_valid, list of error messages)
    """
    errors = []

    if not isinstance(evaluation, dict):
        errors.append("Evaluation is not a valid JSON object")
        return False, errors

    if "Questions" not in evaluation:
        errors.append("Missing 'Questions' in evaluation")
    else:
        questions = evaluation["Questions"]
        if not isinstance(questions, dict):
            errors.append("'Questions' is not a valid object")
        else:
            for q_id, q_data in questions.items():
                prefix = f"Question {q_id}"

                if "Score" not in q_data:
                    errors.append(f"{prefix}: Missing 'Score'")

                if "Sub-part Coverage" not in q_data:
                    errors.append(f"{prefix}: Missing 'Sub-part Coverage'")

                if "Comments" not in q_data:
                    errors.append(f"{prefix}: Missing 'Comments'")
                else:
                    comments = q_data["Comments"]
                    for section in ["Introduction", "Body", "Conclusion"]:
                        if section not in comments:
                            errors.append(f"{prefix}: Missing '{section}' in Comments")
                        else:
                            section_comments = comments[section]
                            if not isinstance(section_comments, list):
                                errors.append(f"{prefix}: '{section}' should be a list")
                            else:
                                for i, comment in enumerate(section_comments):
                                    comment_prefix = f"{prefix} -> {section}[{i}]"
                                    if "page" not in comment:
                                        errors.append(f"{comment_prefix}: Missing 'page'")
                                    if "coordinates" not in comment:
                                        errors.append(f"{comment_prefix}: Missing 'coordinates'")
                                    elif not isinstance(comment["coordinates"], list) or len(comment["coordinates"]) != 4:
                                        errors.append(f"{comment_prefix}: 'coordinates' should be array of 4 values")

                if "HygieneSummary" not in q_data:
                    errors.append(f"{prefix}: Missing 'HygieneSummary'")

                if "Summary" not in q_data:
                    errors.append(f"{prefix}: Missing 'Summary'")

    if "OverallSummary" not in evaluation:
        errors.append("Missing 'OverallSummary' in evaluation")
    elif not isinstance(evaluation["OverallSummary"], list):
        errors.append("'OverallSummary' should be a list")
    elif len(evaluation["OverallSummary"]) == 0:
        errors.append("'OverallSummary' is empty")

    is_valid = len(errors) == 0
    return is_valid, errors


def run_full_pipeline(
    pdf_path: str,
    output_dir: str,
    task_id: str,
    uid: str,
    vertex_api_key: str,
    vertex_project_id: str,
    vertex_location: str,
    vertex_model_name: str,
    openai_api_key: str,
    openai_assistant_id: str,
    progress_callback: Callable[[int, str], None] = None,
) -> Dict[str, Any]:
    """
    Run the full document processing pipeline.

    This is equivalent to running: python run.py document.pdf

    Pipeline steps:
    1. OCR with Gemini (Vertex AI)
    2. Evaluation with OpenAI Assistant
    3. Create annotated PDF
    4. Send evaluation to external API

    Args:
        pdf_path: Path to the input PDF file
        output_dir: Directory to store output files
        task_id: Unique task identifier for naming output files
        uid: Unique identifier for the mains copy (sent to external API)
        vertex_api_key: Vertex AI API key
        vertex_project_id: Vertex AI project ID
        vertex_location: Vertex AI location
        vertex_model_name: Vertex AI model name
        openai_api_key: OpenAI API key
        openai_assistant_id: OpenAI Assistant ID
        progress_callback: Optional callback for progress updates (progress, step)

    Returns:
        Dictionary with result details:
        - status: 'completed' or 'failed'
        - ocr_output_path: Path to OCR JSON
        - evaluation_output_path: Path to evaluation JSON
        - annotated_pdf_path: Path to annotated PDF
        - validation_errors: List of validation errors (if any)
        - external_api_success: Whether external API call succeeded
        - error: Error message (if failed)
    """
    # Create task-specific logger for structured logging
    log = get_task_logger(task_id)

    def update_progress(progress: int, step: str):
        if progress_callback:
            progress_callback(progress, step)
        log.info("[%d%%] %s", progress, step)

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Output file paths
    ocr_output_path = os.path.join(output_dir, f"{task_id}_ocr.json")
    evaluation_output_path = os.path.join(output_dir, f"{task_id}_evaluation.json")
    annotated_pdf_path = os.path.join(output_dir, f"{task_id}_annotated.pdf")

    # Initialize tracking variable for Case 2 (new blank page inserted at start)
    is_new_summary_page_added_at_start = False

    try:
        # Initialize processor
        processor = DocumentProcessor(
            vertex_api_key=vertex_api_key,
            vertex_project_id=vertex_project_id,
            vertex_location=vertex_location,
            vertex_model_name=vertex_model_name,
            openai_api_key=openai_api_key,
            openai_assistant_id=openai_assistant_id,
        )

        # ==============================================================
        # STEP 1: OCR with Gemini (Vertex AI)
        # ==============================================================
        update_progress(10, "ocr_starting")
        log.info("=" * 60)
        log.info("STEP 1: OCR with Gemini")
        log.info("=" * 60)

        ocr_result, metadata = processor.extract_text(pdf_path)

        # Save OCR result with UTF-8 encoding to preserve Devanagari text
        with open(ocr_output_path, "w", encoding="utf-8") as f:
            f.write(ocr_result)

        log.info("✅ OCR Output saved to: %s", ocr_output_path)
        log.info("📄 Processed %d page(s)", len(metadata))

        update_progress(40, "ocr_completed")

        # ==============================================================
        # STEP 1b: Insert Blank Page Based on empty_page_detection
        # ==============================================================
        update_progress(42, "checking_empty_pages")
        log.info("=" * 60)
        log.info("STEP 1b: Checking Empty Page Detection")
        log.info("=" * 60)

        # Parse OCR result to check empty_page_detection
        ocr_data_for_detection = safe_json_loads(ocr_result)
        ocr_data_for_detection = normalize_ocr_data(ocr_data_for_detection)

        # Check for empty_page_detection in OCR response
        empty_page_detection = ocr_data_for_detection.get("empty_page_detection", {})
        insert_blank_page = empty_page_detection.get("insert_blank_page", False)
        blank_page_position = empty_page_detection.get("blank_page_position")
        case_applied = empty_page_detection.get("case_applied", 0)
        summary_page_position = empty_page_detection.get("summary_page_position")

        # Determine if a new summary page was added at the start (Case 2)
        # True = Case 2 (new blank page inserted at position 1)
        # False = Case 1 (using existing empty page) or no action needed
        is_new_summary_page_added_at_start = insert_blank_page and blank_page_position == 1

        log.info("Empty page detection result:")
        log.info("  Page 1 empty %%: %s", empty_page_detection.get("page_1_empty_percentage", "N/A"))
        log.info("  Page 2 empty %%: %s", empty_page_detection.get("page_2_empty_percentage", "N/A"))
        log.info("  Page 3 empty %%: %s", empty_page_detection.get("page_3_empty_percentage", "N/A"))
        log.info("  Case applied: %s", case_applied)
        log.info("  Insert blank page: %s", insert_blank_page)
        log.info("  Blank page position: %s", blank_page_position)
        log.info("  Summary page position: %s", summary_page_position)
        log.info("  Is new summary page added at start: %s", is_new_summary_page_added_at_start)

        # ==============================================================
        # Send is_summary_extra_page_inserted to External API
        # ==============================================================
        log.info("Sending is_summary_extra_page_inserted to external API...")
        try:
            summary_page_payload = {
                "uid": str(uid),
                "data": {
                    "is_summary_extra_page_inserted": is_new_summary_page_added_at_start
                }
            }

            summary_page_response = requests.put(
                EXTERNAL_API_URL,
                json=summary_page_payload,
                headers={'Content-Type': 'application/json'},
                timeout=30,
                verify=False
            )

            if summary_page_response.status_code == 200:
                log.info("✅ External API call successful for is_summary_extra_page_inserted: %s", is_new_summary_page_added_at_start)
            else:
                log.warning("⚠️ External API call for is_summary_extra_page_inserted failed with status: %d", summary_page_response.status_code)
        except Exception as e:
            log.warning("⚠️ External API call for is_summary_extra_page_inserted failed: %s", str(e))

        # PDF path for annotations (may be modified if blank page is inserted)
        pdf_for_annotation = pdf_path

        if insert_blank_page and blank_page_position:
            log.info("📄 Inserting blank A4 page at position %d...", blank_page_position)

            # Create path for modified PDF
            modified_pdf_path = os.path.join(output_dir, f"{task_id}_with_blank_page.pdf")

            # Insert the blank page
            pdf_for_annotation = insert_blank_page_in_pdf(
                pdf_path=pdf_path,
                position=blank_page_position,
                output_path=modified_pdf_path,
                log=log
            )

            log.info("✅ Modified PDF saved to: %s", pdf_for_annotation)

            # ==============================================================
            # STEP 1c: Add Margins and Upload to DO Spaces
            # ==============================================================
            update_progress(43, "adding_margins_and_uploading")
            log.info("=" * 60)
            log.info("STEP 1c: Adding Margins and Uploading to DO Spaces")
            log.info("=" * 60)

            # Create a copy of the PDF with margins for upload
            pdf_with_margins_path = os.path.join(output_dir, f"{task_id}_with_blank_page_and_margins.pdf")

            # Open the blank page PDF, add margins, and save
            log.info("Adding right margin (2.5 inches) and bottom margin (1 inch)...")
            margin_doc = fitz.open(pdf_for_annotation)
            add_margins(margin_doc, right_margin_inches=2.5, bottom_margin_inches=1.0)
            margin_doc.save(pdf_with_margins_path)
            margin_doc.close()
            log.info("✅ Margins added. Saved to: %s", pdf_with_margins_path)

            # Upload to DO Spaces
            destination_path = f"blank-page-pdfs/{uid}_{task_id}_with_blank_page.pdf"
            log.info("Uploading to DO Spaces: %s", destination_path)

            upload_result = upload_to_spaces(pdf_with_margins_path, destination_path)

            if upload_result['status'] == 'success':
                blank_page_pdf_url = upload_result.get('public_url')
                log.info("✅ Upload successful. Public URL: %s", blank_page_pdf_url)

                # Call external API to update with resized_copy_url
                log.info("Calling external API with resized_copy_url...")
                try:
                    external_payload = {
                        "uid": str(uid),
                        "data": {
                            "resized_copy_url": blank_page_pdf_url
                        }
                    }

                    external_response = requests.put(
                        EXTERNAL_API_URL,
                        json=external_payload,
                        headers={'Content-Type': 'application/json'},
                        timeout=30,
                        verify=False
                    )

                    if external_response.status_code == 200:
                        log.info("✅ External API call successful for resized_copy_url")
                    else:
                        log.warning("⚠️ External API call failed with status: %d", external_response.status_code)
                except Exception as e:
                    log.warning("⚠️ External API call failed: %s", str(e))
            else:
                log.warning("⚠️ Upload to DO Spaces failed: %s", upload_result.get('message'))
        else:
            log.info("ℹ️ No blank page insertion needed")

        update_progress(44, "blank_page_check_completed")

        # ==============================================================
        # STEP 2: Evaluation with OpenAI Assistant
        # ==============================================================
        update_progress(45, "evaluation_starting")
        log.info("=" * 60)
        log.info("STEP 2: Evaluation with OpenAI")
        log.info("=" * 60)

        # Parse OCR result (using safe parser to handle Gemini JSON errors)
        ocr_data = safe_json_loads(ocr_result)
        # Normalize OCR data (handle list vs dict formats from Gemini)
        ocr_data = normalize_ocr_data(ocr_data)
        student_text = extract_text_from_ocr(ocr_data)
        student_coords = ocr_result  # Full JSON with coordinates

        log.info("Extracted %d characters of text", len(student_text))
        log.info("Sending to OpenAI Assistant...")

        # For self-evaluation (no model answer), use same text
        model_answer = student_text

        evaluation = processor.evaluate_text_assistant_ai(
            student_text=student_text,
            student_coordinates=student_coords,
            model_answer=model_answer
        )

        # Save evaluation result
        with open(evaluation_output_path, "w") as f:
            json.dump(evaluation, f, indent=2)

        log.info("✅ Evaluation saved to: %s", evaluation_output_path)

        # Validate evaluation JSON structure
        is_valid, validation_errors = validate_evaluation_json(evaluation)

        if is_valid:
            log.info("✅ All required fields are present in evaluation!")
        else:
            log.warning("⚠️ Found %d validation error(s)", len(validation_errors))
            for error in validation_errors:
                log.warning("   %s", error)

        update_progress(75, "evaluation_completed")

        # ==============================================================
        # STEP 3: Create Annotated PDF
        # ==============================================================
        update_progress(75, "annotation_starting")
        log.info("=" * 60)
        log.info("STEP 3: Creating Annotated PDF")
        log.info("=" * 60)
        log.info("Using PDF for annotation: %s", pdf_for_annotation)
        log.info("Summary page position: %s", summary_page_position)
        log.info("Case applied: %s", case_applied)

        # Determine if we're using an existing page for summary (Case 1) or a new blank page (Case 2)
        # Case 1: insert_blank_page=False, using existing page 2 or 3 that has >50% empty but has some content
        # Case 2: insert_blank_page=True, new blank page inserted at position 1
        is_existing_page_for_summary = (case_applied == 1 and not insert_blank_page)
        log.info("Is existing page for summary: %s", is_existing_page_for_summary)

        try:
            # Pass OCR data and metadata for drawing underlines
            # Use pdf_for_annotation which may have blank page inserted
            annotate_pdf_with_comments(
                pdf_for_annotation,  # Use modified PDF with blank page if inserted
                evaluation,
                annotated_pdf_path,
                ocr_data=ocr_data,      # For drawing red underlines
                pages_metadata=metadata,  # For coordinate conversion
                summary_page_position=summary_page_position,  # Page for Overall Summary
                is_existing_page_for_summary=is_existing_page_for_summary  # Whether to place after existing content
            )
            log.info("✅ Annotated PDF saved to: %s", annotated_pdf_path)
        except Exception as e:
            log.warning("⚠️ Warning: Could not create annotated PDF: %s", e)
            annotated_pdf_path = None

        update_progress(85, "annotation_completed")

        # ==============================================================
        # STEP 3b: Upload Annotated PDF to DO Spaces and update verified_copy
        # ==============================================================
        annotated_pdf_url = None
        verified_copy_api_success = False

        if annotated_pdf_path and os.path.exists(annotated_pdf_path):
            update_progress(87, "uploading_annotated_pdf")
            log.info("=" * 60)
            log.info("STEP 3b: Uploading Annotated PDF to DO Spaces")
            log.info("=" * 60)

            # Upload to DO Spaces
            annotated_destination_path = f"annotated-pdfs/{uid}_{task_id}_annotated.pdf"
            log.info("Uploading annotated PDF to DO Spaces: %s", annotated_destination_path)

            annotated_upload_result = upload_to_spaces(annotated_pdf_path, annotated_destination_path)

            if annotated_upload_result['status'] == 'success':
                annotated_pdf_url = annotated_upload_result.get('public_url')
                log.info("✅ Annotated PDF upload successful. Public URL: %s", annotated_pdf_url)

                # Call external API to update with verified_copy
                log.info("Calling external API with verified_copy...")
                try:
                    verified_copy_payload = {
                        "uid": str(uid),
                        "data": {
                            "verified_copy": annotated_pdf_url
                        }
                    }

                    verified_copy_response = requests.put(
                        EXTERNAL_API_URL,
                        json=verified_copy_payload,
                        headers={'Content-Type': 'application/json'},
                        timeout=30,
                        verify=False
                    )

                    if verified_copy_response.status_code == 200:
                        log.info("✅ External API call successful for verified_copy")
                        verified_copy_api_success = True
                    else:
                        log.warning("⚠️ External API call for verified_copy failed with status: %d", verified_copy_response.status_code)
                except Exception as e:
                    log.warning("⚠️ External API call for verified_copy failed: %s", str(e))
            else:
                log.warning("⚠️ Upload annotated PDF to DO Spaces failed: %s", annotated_upload_result.get('message'))
        else:
            log.warning("⚠️ Annotated PDF not available for upload")

        # ==============================================================
        # STEP 4: Send Evaluation to External API
        # ==============================================================
        update_progress(90, "external_api_starting")
        log.info("=" * 60)
        log.info("STEP 4: Sending to External API")
        log.info("=" * 60)

        external_api_success, external_api_message = send_evaluation_to_external_api(
            uid=uid,
            evaluation=evaluation,
            log=log,
            output_dir=output_dir  # Save debug payload for testing
        )

        process_api_success = False
        process_api_message = None

        if external_api_success:
            log.info("✅ External API update successful for uid: %s", uid)

            # Trigger the process API after successful update
            log.info("=" * 60)
            log.info("STEP 4b: Triggering Process API")
            log.info("=" * 60)

            process_api_success, process_api_message = trigger_process_api(log)

            if process_api_success:
                log.info("✅ Process API triggered successfully")
            else:
                log.warning("⚠️ Process API trigger failed: %s", process_api_message)
        else:
            log.warning("⚠️ External API update failed for uid: %s - %s", uid, external_api_message)

        update_progress(100, "completed")

        log.info("=" * 60)
        log.info("✅ PIPELINE COMPLETE!")
        log.info("=" * 60)
        log.info("   OCR Output:      %s", ocr_output_path)
        log.info("   Evaluation:      %s", evaluation_output_path)
        if annotated_pdf_path:
            log.info("   Annotated PDF:   %s", annotated_pdf_path)
        if annotated_pdf_url:
            log.info("   Annotated URL:   %s", annotated_pdf_url)
        log.info("   Verified Copy:   %s", "Success" if verified_copy_api_success else "Failed")
        log.info("   External API:    %s", "Success" if external_api_success else f"Failed - {external_api_message}")
        log.info("   Process API:     %s", "Success" if process_api_success else f"Failed - {process_api_message}" if external_api_success else "Skipped")
        log.info("   New Summary Page Added at Start: %s", is_new_summary_page_added_at_start)

        return {
            'status': 'completed',
            'ocr_output_path': ocr_output_path,
            'evaluation_output_path': evaluation_output_path,
            'annotated_pdf_path': annotated_pdf_path,
            'annotated_pdf_url': annotated_pdf_url,
            'verified_copy_api_success': verified_copy_api_success,
            'validation_errors': validation_errors if not is_valid else [],
            'pages_processed': len(metadata),
            'external_api_success': external_api_success,
            'external_api_message': external_api_message if not external_api_success else None,
            'process_api_success': process_api_success,
            'process_api_message': process_api_message if not process_api_success else None,
            'is_new_summary_page_added_at_start': is_new_summary_page_added_at_start,
        }

    except Exception as e:
        log.error("❌ Pipeline failed: %s", str(e))
        return {
            'status': 'failed',
            'error': str(e),
            'ocr_output_path': ocr_output_path if os.path.exists(ocr_output_path) else None,
            'evaluation_output_path': evaluation_output_path if os.path.exists(evaluation_output_path) else None,
            'annotated_pdf_path': None,
            'external_api_success': False,
            'is_new_summary_page_added_at_start': is_new_summary_page_added_at_start,
        }

