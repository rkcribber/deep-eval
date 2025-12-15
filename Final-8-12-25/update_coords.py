import json

# Load the evaluation JSON
with open('53545_evaluation.json', 'r') as f:
    evaluation = json.load(f)

count = 0
# Process each question's comments
for q_id, q_data in evaluation.get("Questions", {}).items():
    comments = q_data.get("Comments", {})

    for section in ["Introduction", "Body", "Conclusion"]:
        section_comments = comments.get(section, [])

        for comment in section_comments:
            coords = comment.get("coordinates", [])
            if len(coords) == 4:
                # Add +10 to y1 (index 1) and y2 (index 3)
                coords[1] = coords[1] + 10
                coords[3] = coords[3] + 10
                comment["coordinates"] = coords
                count += 1

# Save the updated JSON
with open('53545_evaluation.json', 'w') as f:
    json.dump(evaluation, f, indent=2)

print(f"Updated {count} coordinate sets by adding +10 to y values")

