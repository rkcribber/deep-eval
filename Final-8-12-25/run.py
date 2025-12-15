"""
Document processing and evaluation pipeline.

Usage:
  python run.py <pdf_file>                       # Full pipeline: OCR + OpenAI Evaluation
  python run.py <pdf_file> --ocr-only            # OCR only (no OpenAI)
  python run.py --evaluate <json_file>           # OpenAI evaluation only (from existing JSON)
"""

import sys
import json
from base import DocumentProcessor


def extract_text_from_ocr(ocr_data: dict) -> str:
    """Extract plain text from OCR JSON result."""
    text_parts = []
    for page in ocr_data.get("Pages", []):
        for block in page.get("Blocks", []):
            for line in block.get("Lines", []):
                text = line.get("text", "")
                if text:
                    text_parts.append(text)
    return "\n".join(text_parts)


def validate_evaluation_json(evaluation: dict) -> tuple[bool, list[str]]:
    """
    Validate that the evaluation JSON has all required fields.

    Required structure:
    - Questions (dict)
      - Each question must have:
        - Score
        - Sub-part Coverage
        - Comments (with Introduction, Body, Conclusion)
          - Each comment must have: page, coordinates
        - HygieneSummary
        - Summary
    - OverallSummary (list)

    Returns:
        Tuple of (is_valid, list of error messages)
    """
    errors = []

    # Check if evaluation is a dict
    if not isinstance(evaluation, dict):
        errors.append("❌ Evaluation is not a valid JSON object")
        return False, errors

    # Check for Questions
    if "Questions" not in evaluation:
        errors.append("❌ Missing 'Questions' in evaluation")
    else:
        questions = evaluation["Questions"]
        if not isinstance(questions, dict):
            errors.append("❌ 'Questions' is not a valid object")
        else:
            for q_id, q_data in questions.items():
                prefix = f"Question {q_id}"

                # Check Score
                if "Score" not in q_data:
                    errors.append(f"❌ {prefix}: Missing 'Score'")

                # Check Sub-part Coverage
                if "Sub-part Coverage" not in q_data:
                    errors.append(f"❌ {prefix}: Missing 'Sub-part Coverage'")

                # Check Comments
                if "Comments" not in q_data:
                    errors.append(f"❌ {prefix}: Missing 'Comments'")
                else:
                    comments = q_data["Comments"]

                    # Check Introduction, Body, Conclusion
                    for section in ["Introduction", "Body", "Conclusion"]:
                        if section not in comments:
                            errors.append(f"❌ {prefix}: Missing '{section}' in Comments")
                        else:
                            section_comments = comments[section]
                            if not isinstance(section_comments, list):
                                errors.append(f"❌ {prefix}: '{section}' should be a list")
                            else:
                                for i, comment in enumerate(section_comments):
                                    comment_prefix = f"{prefix} -> {section}[{i}]"

                                    # Check page
                                    if "page" not in comment:
                                        errors.append(f"❌ {comment_prefix}: Missing 'page'")

                                    # Check coordinates
                                    if "coordinates" not in comment:
                                        errors.append(f"❌ {comment_prefix}: Missing 'coordinates'")
                                    elif not isinstance(comment["coordinates"], list) or len(comment["coordinates"]) != 4:
                                        errors.append(f"❌ {comment_prefix}: 'coordinates' should be array of 4 values")

                # Check HygieneSummary
                if "HygieneSummary" not in q_data:
                    errors.append(f"❌ {prefix}: Missing 'HygieneSummary'")

                # Check Summary
                if "Summary" not in q_data:
                    errors.append(f"❌ {prefix}: Missing 'Summary'")

    # Check OverallSummary
    if "OverallSummary" not in evaluation:
        errors.append("❌ Missing 'OverallSummary' in evaluation")
    elif not isinstance(evaluation["OverallSummary"], list):
        errors.append("❌ 'OverallSummary' should be a list")
    elif len(evaluation["OverallSummary"]) == 0:
        errors.append("❌ 'OverallSummary' is empty")

    is_valid = len(errors) == 0
    return is_valid, errors


def print_validation_result(is_valid: bool, errors: list[str]):
    """Print validation results in a formatted way."""
    print("\n" + "=" * 60)
    print("🔍 EVALUATION JSON VALIDATION")
    print("=" * 60)

    if is_valid:
        print("✅ All required fields are present!")
        print("   • Questions with Score, Sub-part Coverage, Comments")
        print("   • Comments have Introduction, Body, Conclusion")
        print("   • Each comment has page and coordinates")
        print("   • HygieneSummary and Summary present")
        print("   • OverallSummary present")
    else:
        print(f"⚠️  Found {len(errors)} validation error(s):\n")
        for error in errors:
            print(f"   {error}")

    print("=" * 60)


def run_ocr_only(pdf_path: str):
    """Run OCR extraction only (no OpenAI evaluation)."""
    print(f"Processing: {pdf_path}")
    print("-" * 50)

    processor = DocumentProcessor()
    result, metadata = processor.extract_text(pdf_path)

    # Save result to JSON file
    output_file = pdf_path.replace(".pdf", "_output.json")
    with open(output_file, "w") as f:
        f.write(result)

    print("-" * 50)
    print(f"✅ OCR Output saved to: {output_file}")
    print(f"📄 Processed {len(metadata)} page(s)")

    for m in metadata:
        status = "converted to A4" if m.was_converted else "already A4"
        print(f"   Page {m.page_number}: {m.original_width_pt:.0f}x{m.original_height_pt:.0f} pt ({status})")

    return result, metadata, output_file


def run_full_pipeline(pdf_path: str):
    """Run full pipeline: OCR with Gemini, then evaluate with OpenAI."""
    processor = DocumentProcessor()

    # Step 1: OCR with Gemini
    print("=" * 60)
    print("STEP 1: OCR with Gemini")
    print("=" * 60)

    result, metadata = processor.extract_text(pdf_path)

    # Save OCR result
    output_file = pdf_path.replace(".pdf", "_output.json")
    with open(output_file, "w") as f:
        f.write(result)

    print("-" * 50)
    print(f"✅ OCR Output saved to: {output_file}")
    print(f"📄 Processed {len(metadata)} page(s)")

    for m in metadata:
        status = "converted to A4" if m.was_converted else "already A4"
        print(f"   Page {m.page_number}: {m.original_width_pt:.0f}x{m.original_height_pt:.0f} pt ({status})")

    # Step 2: Evaluate with OpenAI
    print("\n" + "=" * 60)
    print("STEP 2: Evaluation with OpenAI")
    print("=" * 60)

    # Parse OCR result
    ocr_data = json.loads(result)
    student_text = extract_text_from_ocr(ocr_data)
    student_coords = result  # Full JSON with coordinates

    print(f"Extracted {len(student_text)} characters of text")
    print("Sending to OpenAI Assistant...")

    # For self-evaluation (no model answer), use same text
    model_answer = student_text

    evaluation = processor.evaluate_text_assistant_ai(
        student_text=student_text,
        student_coordinates=student_coords,
        model_answer=model_answer
    )

    # Save evaluation result
    eval_output_file = pdf_path.replace(".pdf", "_evaluation.json")
    with open(eval_output_file, "w") as f:
        json.dump(evaluation, f, indent=2)

    print("-" * 50)
    print(f"✅ Evaluation saved to: {eval_output_file}")

    # Validate evaluation JSON structure
    is_valid, validation_errors = validate_evaluation_json(evaluation)
    print_validation_result(is_valid, validation_errors)

    # Print summary
    print("\n" + "=" * 60)
    print("📊 EVALUATION SUMMARY")
    print("=" * 60)

    if isinstance(evaluation, dict) and "Questions" in evaluation:
        for q_id, q_data in evaluation.get("Questions", {}).items():
            score = q_data.get("Score", "N/A")
            summary = q_data.get("Summary", "No summary")
            print(f"\n{q_id}: Score {score}")
            print(f"   {summary}")

    if isinstance(evaluation, dict) and "OverallSummary" in evaluation:
        print("\n📝 Overall Feedback:")
        for item in evaluation.get("OverallSummary", []):
            print(f"   • {item}")

    # Step 3: Create annotated PDF with comments
    print("\n" + "=" * 60)
    print("STEP 3: Creating Annotated PDF")
    print("=" * 60)

    annotated_pdf_file = pdf_path.replace(".pdf", "_annotated.pdf")
    try:
        annotate_pdf_with_comments(pdf_path, eval_output_file, annotated_pdf_file)
    except Exception as e:
        print(f"⚠️ Warning: Could not create annotated PDF: {e}")
        annotated_pdf_file = None

    print("\n" + "=" * 60)
    print("✅ PIPELINE COMPLETE!")
    print("=" * 60)
    print(f"   OCR Output:      {output_file}")
    print(f"   Evaluation:      {eval_output_file}")
    if annotated_pdf_file:
        print(f"   Annotated PDF:   {annotated_pdf_file}")

    return result, evaluation


def run_evaluation_only(json_path: str):
    """Run OpenAI evaluation only from existing OCR JSON file."""
    processor = DocumentProcessor()

    print("=" * 60)
    print("Evaluation with OpenAI (from existing JSON)")
    print("=" * 60)

    # Load existing OCR result
    with open(json_path, "r") as f:
        ocr_data = json.load(f)

    student_text = extract_text_from_ocr(ocr_data)
    student_coords = json.dumps(ocr_data, indent=2)

    print(f"Loaded: {json_path}")
    print(f"Extracted {len(student_text)} characters of text")
    print("Sending to OpenAI Assistant...")

    model_answer = student_text  # Self-evaluation

    evaluation = processor.evaluate_text_assistant_ai(
        student_text=student_text,
        student_coordinates=student_coords,
        model_answer=model_answer
    )

    # Save evaluation result
    if "_output.json" in json_path:
        eval_output_file = json_path.replace("_output.json", "_evaluation.json")
    else:
        eval_output_file = json_path.replace(".json", "_evaluation.json")

    with open(eval_output_file, "w") as f:
        json.dump(evaluation, f, indent=2)

    print("-" * 50)
    print(f"✅ Evaluation saved to: {eval_output_file}")

    # Validate evaluation JSON structure
    is_valid, validation_errors = validate_evaluation_json(evaluation)
    print_validation_result(is_valid, validation_errors)

    return evaluation


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nExamples:")
        print("  python run.py document.pdf              # Full pipeline (OCR + OpenAI)")
        print("  python run.py document.pdf --ocr-only   # OCR only")
        print("  python run.py --evaluate output.json    # OpenAI evaluation only")
        sys.exit(1)

    # Check for --evaluate flag (evaluation only mode)
    if sys.argv[1] == "--evaluate":
        if len(sys.argv) < 3:
            print("Error: --evaluate requires a JSON file")
            print("Usage: python run.py --evaluate <json_file>")
            sys.exit(1)
        run_evaluation_only(sys.argv[2])

    # Check for --ocr-only flag
    elif len(sys.argv) >= 3 and sys.argv[2] == "--ocr-only":
        run_ocr_only(sys.argv[1])

    # Default: full pipeline (OCR + OpenAI)
    else:
        run_full_pipeline(sys.argv[1])


if __name__ == "__main__":
    main()

