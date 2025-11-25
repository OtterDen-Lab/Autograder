// Rubric table management for autograding

// Render rubric as editable table from JSON
function renderRubricTable(rubricJson) {
    const tableContainer = document.getElementById('rubric-table-container');
    const placeholder = document.getElementById('rubric-placeholder');
    const tbody = document.getElementById('rubric-table-body');

    try {
        const rubricData = JSON.parse(rubricJson);

        // Clear existing rows
        tbody.innerHTML = '';

        // Add rows for each item
        rubricData.items.forEach((item, index) => {
            addRubricRow(item.points, item.description);
        });

        // Show table, hide placeholder
        tableContainer.style.display = 'block';
        placeholder.style.display = 'none';

    } catch (error) {
        console.error('Failed to parse rubric JSON:', error);
        alert('Failed to load rubric');
    }
}

// Add a rubric row to the table
function addRubricRow(points = '', description = '') {
    const tbody = document.getElementById('rubric-table-body');
    const row = document.createElement('tr');

    row.innerHTML = `
        <td style="padding: 8px; border: 1px solid var(--gray-300);">
            <input type="number" class="rubric-points" value="${points}"
                   style="width: 100%; padding: 4px; border: 1px solid var(--gray-300); border-radius: 3px;"
                   min="0" step="1">
        </td>
        <td style="padding: 8px; border: 1px solid var(--gray-300);">
            <input type="text" class="rubric-description" value="${description}"
                   style="width: 100%; padding: 4px; border: 1px solid var(--gray-300); border-radius: 3px;">
        </td>
        <td style="padding: 8px; border: 1px solid var(--gray-300); text-align: center;">
            <button class="remove-rubric-row" style="background: var(--danger-color); color: white; border: none; padding: 4px 8px; border-radius: 3px; cursor: pointer; font-size: 12px;">✕</button>
        </td>
    `;

    // Add remove button handler
    row.querySelector('.remove-rubric-row').onclick = () => {
        row.remove();
    };

    tbody.appendChild(row);
}

// Get rubric as JSON from table
function getRubricFromTable() {
    const tbody = document.getElementById('rubric-table-body');
    const rows = tbody.querySelectorAll('tr');

    const items = [];
    rows.forEach(row => {
        const points = parseFloat(row.querySelector('.rubric-points').value) || 0;
        const description = row.querySelector('.rubric-description').value.trim();

        if (description) {  // Only include rows with descriptions
            items.push({ points, description });
        }
    });

    return JSON.stringify({ items });
}

// Add row button handler
document.getElementById('add-rubric-row-btn').onclick = () => {
    addRubricRow();
};

// Update generate rubric button to use table
document.getElementById('generate-rubric-btn').onclick = async () => {
    const questionText = document.getElementById('autograding-question-text').value;

    if (!questionText.trim()) {
        alert('Please enter the question text first');
        return;
    }

    // Get max points from the UI
    const maxPointsInput = document.getElementById('max-points-input');
    const maxPoints = parseFloat(maxPointsInput.value) || 8;

    // Show loading state
    const rubricLoading = document.getElementById('rubric-loading');
    const tableContainer = document.getElementById('rubric-table-container');
    const placeholder = document.getElementById('rubric-placeholder');
    const generateBtn = document.getElementById('generate-rubric-btn');

    rubricLoading.style.display = 'block';
    tableContainer.style.display = 'none';
    placeholder.style.display = 'none';
    generateBtn.disabled = true;

    try {
        // Generate rubric
        const response = await fetch(`${API_BASE}/ai-grader/${currentSession.id}/generate-rubric`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                problem_number: currentProblemNumber,
                question_text: questionText,
                max_points: maxPoints,
                num_examples: 3
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to generate rubric');
        }

        const data = await response.json();
        renderRubricTable(data.rubric);

    } catch (error) {
        console.error('Failed to generate rubric:', error);
        alert(`Failed to generate rubric: ${error.message}\n\nMake sure you have manually graded at least 3 submissions for this problem first.`);
        placeholder.style.display = 'block';
    } finally {
        rubricLoading.style.display = 'none';
        generateBtn.disabled = false;
    }
};

// Update confirm button to save rubric from table
const originalConfirmHandler = document.getElementById('autograding-confirm-btn').onclick;
document.getElementById('autograding-confirm-btn').onclick = async () => {
    const questionText = document.getElementById('autograding-question-text').value;

    if (!questionText.trim()) {
        alert('Please enter the question text');
        return;
    }

    // Get max points from the UI
    const maxPointsInput = document.getElementById('max-points-input');
    const maxPoints = parseFloat(maxPointsInput.value) || 8;

    // Hide verify phase, show progress phase
    const verifyPhase = document.getElementById('autograding-verify-phase');
    const progressPhase = document.getElementById('autograding-progress-phase');
    verifyPhase.style.display = 'none';
    progressPhase.style.display = 'block';

    // Connect to SSE stream before starting
    connectToAutogradingStream();

    try {
        // Get rubric from table if it exists
        const tableContainer = document.getElementById('rubric-table-container');
        let rubricText = '';
        if (tableContainer.style.display !== 'none') {
            rubricText = getRubricFromTable();
        }

        // Save rubric if provided
        if (rubricText.trim() && rubricText !== '{"items":[]}') {
            await fetch(`${API_BASE}/ai-grader/${currentSession.id}/save-rubric`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    problem_number: currentProblemNumber,
                    rubric: rubricText
                })
            });
        }

        // Start autograding
        const response = await fetch(`${API_BASE}/ai-grader/${currentSession.id}/autograde`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                problem_number: currentProblemNumber,
                question_text: questionText,
                max_points: maxPoints
            })
        });

        if (!response.ok) {
            throw new Error('Failed to start autograding');
        }

        const data = await response.json();
        console.log('Autograding started:', data);

    } catch (error) {
        console.error('Failed to start autograding:', error);
        const modal = document.getElementById('autograding-modal');
        modal.style.display = 'none';
        showNotification(`Failed to start autograding: ${error.message}`);
    }
};

// Load existing rubric when opening autograding modal
const originalStartHandler = document.getElementById('start-autograde-btn').onclick;
// We'll modify this in grading.js to also load the rubric table
