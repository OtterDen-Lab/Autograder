# Manual Exam Alignment Guide

The manual alignment tool allows you to interactively define where exams should be split into questions by clicking on composite images. This is more reliable than automated line detection when:
- Exams have tables or other horizontal lines that confuse detection
- Different students print at different scales
- You want precise control over split points

## How It Works

1. **Upload PDFs**: Upload all exam PDFs for a grading session
2. **Create Composites**: The system overlays all exams for each page with transparency
3. **Aligned content appears darker**: Printed text and consistently-positioned lines stand out
4. **Click to split**: Click on the composite images where you want to split questions
5. **Export**: Save the split points as JSON for use in exam processing

## Using the Manual Alignment Interface

### Step 1: Start the Web Server

```bash
cd Autograder/web_grading
python -m web_api.main
```

The server will start at `http://localhost:8000`

### Step 2: Open the Alignment Interface

Navigate to: **http://localhost:8000/api/alignment/interface**

###Step 3: Upload Exam PDFs

- Click "Choose PDF Files" or drag-and-drop PDF files onto the upload area
- Upload all exams you want to process in this grading session
- The system will handle exams printed at different scales automatically

### Step 4: Create Composite Images

- Click "Create Composite Images"
- The system will:
  - Extract each page from all PDFs
  - Overlay them with transparency (aligned content appears darker)
  - Display one composite image per page number

### Step 5: Mark Split Points

For each page composite:

- **Click** on the image where you want to split questions
  - A red line will appear at that position
  - Lines are numbered (Split 1, Split 2, etc.)

- **Drag** red lines to adjust their position

- **Click** a red line to remove it

**Tips:**
- Look for the dark horizontal lines in the composite (these are consistently positioned across exams)
- Split just **above** each question (the line marks the TOP of the question box)
- If a page has only one question, you don't need to add any splits

### Step 6: Export Split Points

- Click "Export Split Points (JSON)"
- Save the file (default: `exam_split_points.json`)
- This file contains the y-coordinates for splitting, organized by page number

## Using Manual Split Points in Exam Processing

### Option 1: Via Upload API (Recommended for Web Interface)

When uploading exams via the web interface `/api/uploads/process-exams`, include the split points JSON:

```python
# In your upload request
{
    "canvas_course_id": 12345,
    "canvas_assignment_id": 67890,
    "manual_split_points": {
        "0": [120, 315],  # Page 0 (first page) splits at y=120 and y=315
        "1": [73, 267],   # Page 1 splits at y=73 and y=267
        ...
    }
}
```

### Option 2: Directly in Python Code

```python
from pathlib import Path
import json
from Autograder.web_grading.web_api.services.exam_processor import ExamProcessor
from Autograder.web_grading.web_api.services.manual_alignment import ManualAlignmentService

# Load split points from JSON
alignment_service = ManualAlignmentService()
split_points = alignment_service.load_split_points(Path("exam_split_points.json"))

# Process exams with manual splits
processor = ExamProcessor()
matched, unmatched = processor.process_exams(
    input_files=[Path("exam1.pdf"), Path("exam2.pdf"), ...],
    canvas_students=[...],
    manual_split_points=split_points  # Use manual splits instead of auto-detection
)
```

## Split Points JSON Format

```json
{
  "version": "1.0",
  "num_exams": 25,
  "split_points": {
    "0": [120, 315, 450],  // Page 0: Split at y=120, y=315, y=450
    "1": [73, 267, 420],   // Page 1: Split at y=73, y=267, y=420
    "2": [78],             // Page 2: Split at y=78
    ...
  }
}
```

- **Page numbers** are 0-indexed (0 = first page)
- **Y-positions** are in PDF coordinate space (points from top of page)
- Each split marks the **TOP** of a question region

## Workflow Example

```bash
# 1. Start the server
cd Autograder/web_grading
python -m web_api.main

# 2. Open browser
open http://localhost:8000/api/alignment/interface

# 3. Upload all exam PDFs → Create Composites → Click split points → Export JSON

# 4. Use the split points in your grading workflow
# (Either via web UI or programmatically)
```

## Tips for Best Results

### Creating Composites
- Upload **all exams** from the same exam template together
- The more exams, the clearer the aligned content will appear
- Different scales are handled automatically through normalization

### Marking Split Points
- Split just **above** each question (the line is the top border)
- Look for dark horizontal lines in composite (consistently aligned across exams)
- You can skip pages with only one question (no splits needed)
- Test with 2-3 exams first to verify splits are correct

### Reusing Split Points
- Save the JSON file with a descriptive name (`midterm_fall2025_splits.json`)
- Reuse the same split points for all future exams with the same template
- Only recreate if you change the exam format

## Troubleshooting

**Composite images look fuzzy/unclear**
- This is normal - the overlay effect emphasizes aligned content
- Dark lines and text indicate consistently positioned elements
- Focus on the darker horizontal lines for split points

**Not sure where to split**
- Look at the debug PDF from `debug_line_detection.py` for reference
- Check one individual exam PDF to see question boundaries
- When in doubt, split conservatively (fewer splits = larger question regions)

**Splits not working correctly**
- Verify the JSON format matches the example above
- Check that page numbers are integers (not strings with quotes)
- Ensure y-positions are sorted from top to bottom

## Next Steps: QR Code Detection

For even more reliable splitting, consider adding QR codes to your exam template:
- Place a small QR code in the upper-right corner of each question
- Encode the question number in the QR payload
- Future enhancement will auto-detect and split based on QR codes
