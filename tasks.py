"""
Celery Tasks - Long running operations go here

This module runs the document processing pipeline:
1. Download PDF from student_uploaded_pdf_url
2. OCR with Vertex AI / Gemini
3. Evaluation with OpenAI Assistant
4. Create annotated PDF with comments
"""
import os
import shutil
import requests
import urllib3
from celery_app import celery
from config import (
    VERTEX_AI_API_KEY,
    VERTEX_PROJECT_ID,
    VERTEX_LOCATION,
    VERTEX_MODEL_NAME,
    OPENAI_API_KEY,
    OPENAI_ASSISTANT_ID,
)
from processing.pipeline import run_full_pipeline
from logger import get_task_logger

# Suppress SSL verification warnings (we use verify=False for DO Spaces compatibility)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Base directory for all task processing
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Directory for temporary files during processing (per-task subdirectories)
TMP_DIR = os.path.join(BASE_DIR, 'tmp')
os.makedirs(TMP_DIR, exist_ok=True)

# Directory for final output files (annotated PDFs)
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')
os.makedirs(OUTPUT_DIR, exist_ok=True)


def download_pdf(url: str, task_dir: str, task_id: str) -> str:
    """
    Download PDF from URL and save to task-specific temp directory.

    Args:
        url: Public URL of the PDF file
        task_dir: Task-specific temporary directory
        task_id: Task ID used for unique filename

    Returns:
        Local file path of the downloaded PDF
    """
    filename = f"{task_id}_input.pdf"
    filepath = os.path.join(task_dir, filename)

    # Download the file
    # Note: verify=False disables SSL verification - used as workaround for
    # certificate issues in Docker containers with DigitalOcean Spaces
    response = requests.get(url, stream=True, timeout=120, verify=False)
    response.raise_for_status()

    # Save to disk
    with open(filepath, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    return filepath


def cleanup_task_dir(task_dir: str, keep_files: list = None):
    """
    Clean up task temporary directory, optionally keeping specified files.

    Args:
        task_dir: Task-specific temporary directory to clean
        keep_files: List of file paths to keep (will be moved to output)
    """
    if keep_files:
        for filepath in keep_files:
            if filepath and os.path.exists(filepath):
                # File is already in output dir, no action needed
                pass

    # Remove the entire task temp directory
    if os.path.exists(task_dir):
        shutil.rmtree(task_dir)


@celery.task(bind=True, name='tasks.process_data')
def process_data_task(self, data: dict) -> dict:
    """
    Process student PDF through the document evaluation pipeline.

    Pipeline Steps:
    1. Download PDF from student_uploaded_pdf_url
    2. OCR with Vertex AI / Gemini
    3. Evaluate with OpenAI Assistant
    4. Create annotated PDF with comments
    5. Cleanup temporary files

    Input Data:
        student_uploaded_pdf_url (required): Public URL to student's PDF

    Output Files:
        - {task_id}_annotated.pdf: Final annotated PDF (in output/ directory)
        - {task_id}_ocr.json: OCR output (in output/ directory)
        - {task_id}_evaluation.json: Evaluation result (in output/ directory)

    Returns:
        dict with status, paths to output files, and any errors
    """
    task_id = self.request.id

    # Create task-specific logger for structured logging
    log = get_task_logger(task_id)
    log.info("Task started")

    # Create task-specific temp directory for intermediate files
    task_dir = os.path.join(TMP_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    def update_progress(progress: int, step: str):
        self.update_state(state='PROCESSING', meta={'progress': progress, 'step': step})
        log.info("Progress: %d%% - %s", progress, step)

    update_progress(0, 'starting')

    try:
        # ===========================================
        # STEP 1: Validate Input
        # ===========================================
        pdf_url = data.get('student_uploaded_pdf_url')
        if not pdf_url:
            log.error("Missing required field: student_uploaded_pdf_url")
            return {
                'status': 'failed',
                'task_id': task_id,
                'error': 'Missing required field: student_uploaded_pdf_url'
            }

        uid = data.get('uid')
        if not uid:
            log.error("Missing required field: uid")
            return {
                'status': 'failed',
                'task_id': task_id,
                'error': 'Missing required field: uid'
            }

        # Validate API keys are configured
        if not VERTEX_AI_API_KEY:
            log.error("VERTEX_AI_API_KEY not configured")
            return {
                'status': 'failed',
                'task_id': task_id,
                'error': 'VERTEX_AI_API_KEY not configured'
            }
        if not OPENAI_API_KEY or not OPENAI_ASSISTANT_ID:
            log.error("OPENAI_API_KEY or OPENAI_ASSISTANT_ID not configured")
            return {
                'status': 'failed',
                'task_id': task_id,
                'error': 'OPENAI_API_KEY or OPENAI_ASSISTANT_ID not configured'
            }

        log.info("Processing for uid: %s", uid)

        # Get optional model_answer_url
        model_answer_url = data.get('model_answer_url')
        if model_answer_url:
            log.info("Model answer URL provided: %s", model_answer_url[:100] + "..." if len(model_answer_url) > 100 else model_answer_url)

        # ===========================================
        # STEP 2: Download PDF
        # ===========================================
        update_progress(5, 'downloading_pdf')
        log.info("Downloading PDF from: %s", pdf_url[:100] + "..." if len(pdf_url) > 100 else pdf_url)
        pdf_path = download_pdf(pdf_url, task_dir, task_id)
        log.info("PDF downloaded to: %s", pdf_path)
        update_progress(10, 'pdf_downloaded')

        # ===========================================
        # STEP 3: Run Processing Pipeline
        # (OCR -> Evaluation -> Annotated PDF -> External API)
        # ===========================================
        log.info("Starting processing pipeline...")
        result = run_full_pipeline(
            pdf_path=pdf_path,
            output_dir=OUTPUT_DIR,  # Final outputs go to output/ directory
            task_id=task_id,
            uid=uid,
            vertex_api_key=VERTEX_AI_API_KEY,
            vertex_project_id=VERTEX_PROJECT_ID,
            vertex_location=VERTEX_LOCATION,
            vertex_model_name=VERTEX_MODEL_NAME,
            openai_api_key=OPENAI_API_KEY,
            openai_assistant_id=OPENAI_ASSISTANT_ID,
            progress_callback=update_progress,
            model_answer_url=model_answer_url,
        )

        # ===========================================
        # STEP 4: Cleanup Temporary Files
        # ===========================================
        log.info("Cleaning up temporary files...")
        cleanup_task_dir(task_dir)

        # ===========================================
        # STEP 5: Return Result
        # ===========================================
        if result['status'] == 'completed':
            log.info("Task completed successfully. Pages processed: %d, External API: %s",
                    result.get('pages_processed', 0),
                    "Success" if result.get('external_api_success') else "Failed")
            return {
                'status': 'completed',
                'task_id': task_id,
                'uid': uid,
                'annotated_pdf_path': result.get('annotated_pdf_path'),
                'ocr_output_path': result.get('ocr_output_path'),
                'evaluation_output_path': result.get('evaluation_output_path'),
                'pages_processed': result.get('pages_processed', 0),
                'validation_errors': result.get('validation_errors', []),
                'external_api_success': result.get('external_api_success', False),
                'external_api_message': result.get('external_api_message'),
            }
        else:
            log.error("Pipeline failed: %s", result.get('error', 'Unknown error'))
            return {
                'status': 'failed',
                'task_id': task_id,
                'error': result.get('error', 'Unknown error in pipeline'),
            }

    except requests.RequestException as e:
        log.error("Failed to download PDF: %s", str(e))
        cleanup_task_dir(task_dir)
        return {
            'status': 'failed',
            'task_id': task_id,
            'error': f'Failed to download PDF: {str(e)}'
        }
    except Exception as e:
        log.error("Unexpected error: %s", str(e))
        cleanup_task_dir(task_dir)
        return {
            'status': 'failed',
            'task_id': task_id,
            'error': str(e)
        }

