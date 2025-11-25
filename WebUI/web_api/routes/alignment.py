"""
Manual alignment endpoints for interactive split point selection
"""
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse
from typing import List, Dict
from pydantic import BaseModel
import tempfile
import shutil
from pathlib import Path

from ..services.manual_alignment import ManualAlignmentService

router = APIRouter()
alignment_service = ManualAlignmentService()


class SplitPointsUpdate(BaseModel):
  """Model for updating split points"""
  split_points: Dict[int, List[int]]


@router.post("/create-composites")
async def create_composite_images(files: List[UploadFile] = File(...)):
  """
    Create composite overlay images from uploaded exam PDFs.

    Args:
        files: List of PDF files to process

    Returns:
        Dict with page_number -> base64 image for each page
    """
  if not files:
    raise HTTPException(status_code=400, detail="No files provided")

  # Create temp directory for uploaded files
  with tempfile.TemporaryDirectory() as temp_dir:
    temp_path = Path(temp_dir)
    pdf_paths = []

    # Save uploaded files
    for file in files:
      if not file.filename.endswith('.pdf'):
        raise HTTPException(
          status_code=400,
          detail=
          f"Invalid file type: {file.filename}. Only PDF files are accepted.")

      file_path = temp_path / file.filename
      with open(file_path, 'wb') as f:
        shutil.copyfileobj(file.file, f)
      pdf_paths.append(file_path)

    # Generate composite images
    composites = alignment_service.create_composite_images(pdf_paths)

    # Get page dimensions from first PDF
    import fitz
    first_pdf = fitz.open(str(pdf_paths[0]))
    page_dimensions = {}
    for page_num in range(first_pdf.page_count):
      page = first_pdf[page_num]
      page_dimensions[page_num] = {
        "width": page.rect.width,
        "height": page.rect.height
      }
    first_pdf.close()

    return {
      "composites": composites,
      "page_dimensions": page_dimensions,
      "num_exams": len(pdf_paths)
    }


@router.get("/interface", response_class=HTMLResponse)
async def get_alignment_interface():
  """
    Serve the interactive alignment interface HTML page.
    """
  html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Manual Exam Alignment</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f5f5f5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        h1 {
            color: #333;
            margin-top: 0;
        }
        .upload-section {
            margin-bottom: 30px;
            padding: 20px;
            border: 2px dashed #ccc;
            border-radius: 8px;
            text-align: center;
        }
        .upload-section.dragging {
            border-color: #4CAF50;
            background: #f0f8f0;
        }
        .page-section {
            margin-bottom: 40px;
            border: 1px solid #ddd;
            border-radius: 8px;
            overflow: hidden;
        }
        .page-header {
            background: #4CAF50;
            color: white;
            padding: 15px;
            font-weight: bold;
        }
        .canvas-container {
            position: relative;
            margin: 20px;
            cursor: crosshair;
        }
        canvas {
            border: 1px solid #ccc;
            max-width: 100%;
            height: auto;
        }
        .split-line {
            position: absolute;
            left: 0;
            right: 0;
            height: 3px;
            background: red;
            cursor: move;
            opacity: 0.7;
        }
        .split-line:hover {
            opacity: 1;
            height: 5px;
        }
        .split-line-label {
            position: absolute;
            right: 5px;
            top: -20px;
            background: red;
            color: white;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 12px;
            pointer-events: none;
        }
        .controls {
            display: flex;
            gap: 10px;
            margin: 20px 0;
            flex-wrap: wrap;
        }
        button {
            padding: 10px 20px;
            background: #4CAF50;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
        }
        button:hover {
            background: #45a049;
        }
        button:disabled {
            background: #ccc;
            cursor: not-allowed;
        }
        button.secondary {
            background: #2196F3;
        }
        button.secondary:hover {
            background: #0b7dda;
        }
        button.danger {
            background: #f44336;
        }
        button.danger:hover {
            background: #da190b;
        }
        .status {
            margin-top: 20px;
            padding: 15px;
            background: #f0f0f0;
            border-radius: 4px;
        }
        .help-text {
            color: #666;
            font-size: 14px;
            margin-top: 10px;
        }
        .loading {
            text-align: center;
            padding: 40px;
            color: #666;
        }
        input[type="file"] {
            display: none;
        }
        .file-label {
            display: inline-block;
            padding: 10px 20px;
            background: #4CAF50;
            color: white;
            border-radius: 4px;
            cursor: pointer;
        }
        .file-label:hover {
            background: #45a049;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>📏 Manual Exam Alignment</h1>

        <div id="upload-section" class="upload-section">
            <h2>Step 1: Upload Exam PDFs</h2>
            <label for="file-input" class="file-label">Choose PDF Files</label>
            <input type="file" id="file-input" multiple accept=".pdf">
            <p class="help-text">or drag and drop PDF files here</p>
            <div id="file-list"></div>
        </div>

        <div class="controls">
            <button id="process-btn" disabled>Create Composite Images</button>
            <button id="export-btn" class="secondary" disabled>Export Split Points (JSON)</button>
            <button id="clear-btn" class="danger" disabled>Clear All</button>
        </div>

        <div id="status" class="status" style="display: none;"></div>

        <div id="pages-container"></div>
    </div>

    <script>
        let compositeData = null;
        let splitPoints = {};  // page_num -> [y_positions]
        let selectedFiles = [];

        const uploadSection = document.getElementById('upload-section');
        const fileInput = document.getElementById('file-input');
        const fileList = document.getElementById('file-list');
        const processBtn = document.getElementById('process-btn');
        const exportBtn = document.getElementById('export-btn');
        const clearBtn = document.getElementById('clear-btn');
        const pagesContainer = document.getElementById('pages-container');
        const statusDiv = document.getElementById('status');

        // File input handling
        fileInput.addEventListener('change', handleFiles);

        // Drag and drop
        uploadSection.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadSection.classList.add('dragging');
        });

        uploadSection.addEventListener('dragleave', () => {
            uploadSection.classList.remove('dragging');
        });

        uploadSection.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadSection.classList.remove('dragging');
            const files = Array.from(e.dataTransfer.files).filter(f => f.name.endsWith('.pdf'));
            fileInput.files = createFileList(files);
            handleFiles({ target: fileInput });
        });

        function createFileList(files) {
            const dt = new DataTransfer();
            files.forEach(f => dt.items.add(f));
            return dt.files;
        }

        function handleFiles(e) {
            selectedFiles = Array.from(e.target.files);
            if (selectedFiles.length > 0) {
                fileList.innerHTML = `<p>${selectedFiles.length} PDF file(s) selected</p>`;
                processBtn.disabled = false;
            }
        }

        // Process button
        processBtn.addEventListener('click', async () => {
            showStatus('Creating composite images...', 'info');
            processBtn.disabled = true;

            const formData = new FormData();
            selectedFiles.forEach(file => formData.append('files', file));

            try {
                const response = await fetch('/api/alignment/create-composites', {
                    method: 'POST',
                    body: formData
                });

                if (!response.ok) {
                    throw new Error('Failed to create composites');
                }

                compositeData = await response.json();
                renderComposites();
                showStatus(`Successfully processed ${compositeData.num_exams} exams`, 'success');
                exportBtn.disabled = false;
                clearBtn.disabled = false;
            } catch (error) {
                showStatus(`Error: ${error.message}`, 'error');
                processBtn.disabled = false;
            }
        });

        function renderComposites() {
            pagesContainer.innerHTML = '';
            splitPoints = {};

            for (const [pageNum, imageBase64] of Object.entries(compositeData.composites)) {
                const pageSection = createPageSection(parseInt(pageNum), imageBase64);
                pagesContainer.appendChild(pageSection);
            }
        }

        function createPageSection(pageNum, imageBase64) {
            const section = document.createElement('div');
            section.className = 'page-section';
            section.id = `page-${pageNum}`;

            const header = document.createElement('div');
            header.className = 'page-header';
            header.textContent = `Page ${pageNum + 1}`;

            const canvasContainer = document.createElement('div');
            canvasContainer.className = 'canvas-container';

            const canvas = document.createElement('canvas');
            canvas.id = `canvas-${pageNum}`;

            const img = new Image();
            img.onload = () => {
                canvas.width = img.width;
                canvas.height = img.height;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0);
            };
            img.src = `data:image/png;base64,${imageBase64}`;

            // Click to add split line
            canvas.addEventListener('click', (e) => {
                const rect = canvas.getBoundingClientRect();
                const scaleX = canvas.width / rect.width;
                const scaleY = canvas.height / rect.height;
                const y = (e.clientY - rect.top) * scaleY;

                // Convert from image coordinates (150 DPI) to PDF coordinates (72 DPI)
                const pageDims = compositeData.page_dimensions[pageNum];
                const pdfY = Math.round((y / canvas.height) * pageDims.height);

                addSplitLine(pageNum, pdfY, canvasContainer);
            });

            canvasContainer.appendChild(canvas);

            const helpText = document.createElement('p');
            helpText.className = 'help-text';
            helpText.innerHTML = `
                <strong>Click</strong> on the composite image to add split points.<br>
                <strong>Drag</strong> the red lines to adjust them.<br>
                <strong>Click</strong> a line to remove it.
            `;

            section.appendChild(header);
            section.appendChild(helpText);
            section.appendChild(canvasContainer);

            return section;
        }

        function addSplitLine(pageNum, pdfY, container) {
            if (!splitPoints[pageNum]) {
                splitPoints[pageNum] = [];
            }

            splitPoints[pageNum].push(pdfY);
            splitPoints[pageNum].sort((a, b) => a - b);

            updateSplitLines(pageNum, container);
        }

        function updateSplitLines(pageNum, container) {
            // Remove existing lines
            container.querySelectorAll('.split-line').forEach(el => el.remove());

            const canvas = container.querySelector('canvas');
            const pageDims = compositeData.page_dimensions[pageNum];

            splitPoints[pageNum].forEach((pdfY, idx) => {
                // Convert from PDF coordinates to canvas pixel coordinates
                const canvasY = (pdfY / pageDims.height) * canvas.height;

                const line = document.createElement('div');
                line.className = 'split-line';
                line.style.top = `${canvasY}px`;

                const label = document.createElement('div');
                label.className = 'split-line-label';
                label.textContent = `Split ${idx + 1}`;

                // Click to remove
                line.addEventListener('click', (e) => {
                    e.stopPropagation();
                    splitPoints[pageNum] = splitPoints[pageNum].filter(y => y !== pdfY);
                    if (splitPoints[pageNum].length === 0) {
                        delete splitPoints[pageNum];
                    }
                    updateSplitLines(pageNum, container);
                });

                line.appendChild(label);
                container.appendChild(line);
            });
        }

        // Export split points
        exportBtn.addEventListener('click', () => {
            const data = {
                version: "1.0",
                num_exams: compositeData.num_exams,
                split_points: splitPoints
            };

            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'exam_split_points.json';
            a.click();
            URL.revokeObjectURL(url);

            showStatus('Split points exported successfully', 'success');
        });

        // Clear all
        clearBtn.addEventListener('click', () => {
            if (confirm('Clear all data and start over?')) {
                compositeData = null;
                splitPoints = {};
                selectedFiles = [];
                pagesContainer.innerHTML = '';
                fileList.innerHTML = '';
                fileInput.value = '';
                processBtn.disabled = true;
                exportBtn.disabled = true;
                clearBtn.disabled = true;
                statusDiv.style.display = 'none';
            }
        });

        function showStatus(message, type) {
            statusDiv.textContent = message;
            statusDiv.style.display = 'block';
            statusDiv.style.background = type === 'error' ? '#ffebee' :
                                        type === 'success' ? '#e8f5e9' : '#f0f0f0';
            statusDiv.style.color = type === 'error' ? '#c62828' :
                                   type === 'success' ? '#2e7d32' : '#666';
        }
    </script>
</body>
</html>
    """
  return HTMLResponse(content=html_content)
