"""
DigitalOcean Spaces Integration

Handles uploading files to DigitalOcean Spaces and generating public URLs.
Includes PDF compression to save storage space.
"""

import os
import logging
import tempfile
import boto3
from botocore.client import Config
import urllib3
import fitz  # PyMuPDF

# Disable SSL warnings (for environments with certificate issues)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# DigitalOcean Spaces configuration from environment variables
DO_SPACES_KEY = os.environ.get('DO_SPACES_KEY', '')
DO_SPACES_SECRET = os.environ.get('DO_SPACES_SECRET', '')
DO_SPACES_REGION = os.environ.get('DO_SPACES_REGION', 'sfo3')
DO_SPACES_BUCKET = os.environ.get('DO_SPACES_BUCKET', '')
DO_SPACES_ENDPOINT = os.environ.get('DO_SPACES_ENDPOINT', f'https://{DO_SPACES_REGION}.digitaloceanspaces.com')


def compress_pdf(input_path: str, output_path: str = None) -> str:
    """
    Compress a PDF file to reduce file size.

    Args:
        input_path: Path to the input PDF file
        output_path: Path for the compressed PDF (default: temp file)

    Returns:
        Path to the compressed PDF file
    """
    try:
        original_size = os.path.getsize(input_path)
        logger.info("[do_spaces] Compressing PDF: %s (original size: %.2f KB)",
                   input_path, original_size / 1024)

        # Open the PDF
        doc = fitz.open(input_path)

        # Create output path if not provided
        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix='.pdf')
            os.close(fd)

        # Save with compression options
        # garbage=4: maximum garbage collection (removes unused objects)
        # deflate=True: compress streams
        # clean=True: clean content streams
        doc.save(
            output_path,
            garbage=4,      # Maximum garbage collection
            deflate=True,   # Compress streams
            clean=True,     # Clean content streams
            linear=True,    # Linearize for web (fast web view)
        )
        doc.close()

        compressed_size = os.path.getsize(output_path)
        reduction = ((original_size - compressed_size) / original_size) * 100 if original_size > 0 else 0

        logger.info("[do_spaces] ✅ PDF compressed: %.2f KB -> %.2f KB (%.1f%% reduction)",
                   original_size / 1024, compressed_size / 1024, reduction)

        return output_path

    except Exception as e:
        logger.warning("[do_spaces] Failed to compress PDF: %s. Using original file.", str(e))
        return input_path


def get_spaces_client():
    """
    Create and return a boto3 client for DigitalOcean Spaces.

    Returns:
        boto3 client configured for DO Spaces
    """
    client = boto3.client(
        's3',
        region_name=DO_SPACES_REGION,
        endpoint_url=DO_SPACES_ENDPOINT,
        aws_access_key_id=DO_SPACES_KEY,
        aws_secret_access_key=DO_SPACES_SECRET,
        config=Config(signature_version='s3v4'),
        verify=False  # Disable SSL verification for environments with cert issues
    )
    return client


def upload_to_spaces(file_path: str, destination_path: str = None, content_type: str = 'application/pdf', compress: bool = True) -> dict:
    """
    Upload a file to DigitalOcean Spaces.

    Args:
        file_path: Local path to the file to upload
        destination_path: Path/key in the bucket (default: uses filename)
        content_type: MIME type of the file (default: application/pdf)
        compress: Whether to compress PDF files before upload (default: True)

    Returns:
        Dictionary with status, public_url, and message
    """
    if not all([DO_SPACES_KEY, DO_SPACES_SECRET, DO_SPACES_BUCKET]):
        logger.error("[do_spaces] Missing DO Spaces configuration")
        return {
            'status': 'error',
            'message': 'DigitalOcean Spaces not configured. Missing environment variables.',
            'public_url': None
        }

    compressed_path = None
    upload_path = file_path

    try:
        # Use filename if no destination path provided
        if destination_path is None:
            destination_path = os.path.basename(file_path)

        # Compress PDF if enabled and file is a PDF
        if compress and content_type == 'application/pdf' and file_path.lower().endswith('.pdf'):
            compressed_path = compress_pdf(file_path)
            if compressed_path != file_path:
                upload_path = compressed_path

        logger.info("[do_spaces] Uploading file to DO Spaces: %s -> %s", upload_path, destination_path)

        client = get_spaces_client()

        # Upload the file with public-read ACL
        with open(upload_path, 'rb') as file_data:
            client.put_object(
                Bucket=DO_SPACES_BUCKET,
                Key=destination_path,
                Body=file_data,
                ACL='public-read',
                ContentType=content_type
            )

        # Generate public URL
        # Format: https://{bucket}.{region}.digitaloceanspaces.com/{key}
        public_url = f"https://{DO_SPACES_BUCKET}.{DO_SPACES_REGION}.digitaloceanspaces.com/{destination_path}"

        logger.info("[do_spaces] File uploaded successfully. Public URL: %s", public_url)

        return {
            'status': 'success',
            'message': 'File uploaded successfully',
            'public_url': public_url
        }

    except Exception as e:
        logger.error("[do_spaces] Failed to upload file: %s", str(e))
        return {
            'status': 'error',
            'message': f'Failed to upload to DO Spaces: {str(e)}',
            'public_url': None
        }

    finally:
        # Clean up compressed temp file if it was created
        if compressed_path and compressed_path != file_path:
            try:
                os.remove(compressed_path)
                logger.debug("[do_spaces] Cleaned up compressed temp file: %s", compressed_path)
            except:
                pass


def delete_from_spaces(file_key: str) -> dict:
    """
    Delete a file from DigitalOcean Spaces.

    Args:
        file_key: The key/path of the file in the bucket

    Returns:
        Dictionary with status and message
    """
    if not all([DO_SPACES_KEY, DO_SPACES_SECRET, DO_SPACES_BUCKET]):
        return {
            'status': 'error',
            'message': 'DigitalOcean Spaces not configured'
        }

    try:
        client = get_spaces_client()
        client.delete_object(Bucket=DO_SPACES_BUCKET, Key=file_key)

        logger.info("[do_spaces] File deleted: %s", file_key)

        return {
            'status': 'success',
            'message': 'File deleted successfully'
        }

    except Exception as e:
        logger.error("[do_spaces] Failed to delete file: %s", str(e))
        return {
            'status': 'error',
            'message': f'Failed to delete from DO Spaces: {str(e)}'
        }

