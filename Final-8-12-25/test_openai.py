"""
Test OpenAI evaluation with OCR output JSON

Usage:
  python test_openai.py                     # Uses 53545_output.json by default
  python test_openai.py <json_file>         # Uses specified JSON file
"""

import sys
import json
from base import DocumentProcessor


def main():
    # Get JSON file from command line or use default
    json_file = sys.argv[1] if len(sys.argv) > 1 else "53545_output.json"

    # Load the OCR output
    with open(json_file, "r") as f:
        ocr_data = json.load(f)

    # Extract text from OCR
    text_parts = []
    for page in ocr_data.get("Pages", []):
        for block in page.get("Blocks", []):
            for line in block.get("Lines", []):
                text = line.get("text", "")
                if text:
                    text_parts.append(text)

    student_text = "\n".join(text_parts)
    student_coords = json.dumps(ocr_data, indent=2)

    print("=" * 60)
    print("STUDENT TEXT EXTRACTED:")
    print("=" * 60)
    print(student_text[:1000])  # Print first 1000 chars
    print("..." if len(student_text) > 1000 else "")
    print(f"\nTotal characters: {len(student_text)}")

    # For testing, use the same text as model answer
    # In real usage, you would have a separate model answer
    model_answer = student_text  # Using same for test

    print("\n" + "=" * 60)
    print("SENDING TO OPENAI FOR EVALUATION...")
    print("=" * 60)

    processor = DocumentProcessor()

    try:
        evaluation = processor.evaluate_text_assistant_ai(
            student_text=student_text,
            student_coordinates=student_coords,
            model_answer=model_answer
        )

        # Save evaluation result
        output_file = json_file.replace("_output.json", "_evaluation.json")
        with open(output_file, "w") as f:
            json.dump(evaluation, f, indent=2)

        print("\n" + "=" * 60)
        print("✅ EVALUATION COMPLETE!")
        print("=" * 60)
        print(f"Result saved to: {output_file}")
        print("\nEvaluation result:")
        print(json.dumps(evaluation, indent=2)[:2000])  # Print first 2000 chars

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        raise


if __name__ == "__main__":
    main()

