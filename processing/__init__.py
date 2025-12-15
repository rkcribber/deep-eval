"""
Processing module for document OCR and evaluation pipeline.

This module provides:
1. DocumentProcessor - OCR extraction using Vertex AI / Gemini
2. Evaluation using OpenAI Assistant API
3. PDF annotation with evaluation comments
"""
from .document_processor import DocumentProcessor
from .annotate_pdf import annotate_pdf_with_comments
from .pipeline import run_full_pipeline

__all__ = ['DocumentProcessor', 'annotate_pdf_with_comments', 'run_full_pipeline']

