// Review Grades Functionality
// Allows reviewing and editing already-graded submissions

let reviewProblems = [];
let reviewCurrentIndex = 0;
let reviewProblemNumber = null;

// Open review dialog
document.getElementById('review-grades-btn').addEventListener('click', async () => {
    if (!currentSession || !currentProblemNumber) return;

    reviewProblemNumber = currentProblemNumber;
    const maxPoints = problemMaxPoints[currentProblemNumber] || 8;

    // Update modal title and max points
    document.getElementById('review-problem-number').textContent = reviewProblemNumber;
    document.getElementById('review-score-slider').max = maxPoints;
    document.getElementById('review-score-input').max = maxPoints;

    // Load graded problems
    try {
        const response = await fetch(
            `${API_BASE}/problems/${currentSession.id}/${reviewProblemNumber}/graded?limit=100`
        );

        if (!response.ok) {
            throw new Error('Failed to load graded problems');
        }

        const data = await response.json();
        reviewProblems = data.problems;

        if (reviewProblems.length === 0) {
            alert(`No graded submissions found for Problem ${reviewProblemNumber}`);
            return;
        }

        // Show dialog
        document.getElementById('review-dialog').style.display = 'flex';

        // Load first problem
        reviewCurrentIndex = 0;
        await loadReviewProblem(reviewCurrentIndex);

    } catch (error) {
        console.error('Failed to open review mode:', error);
        alert('Failed to load graded problems: ' + error.message);
    }
});

// Close review dialog
document.getElementById('close-review-btn').addEventListener('click', () => {
    document.getElementById('review-dialog').style.display = 'none';
    // Reload the main grading view to pick up any changes
    loadProblemOrMostRecent();
});

// Load a specific problem in review mode
async function loadReviewProblem(index) {
    if (index < 0 || index >= reviewProblems.length) return;

    reviewCurrentIndex = index;
    const problemMeta = reviewProblems[index];

    // Update navigation info
    document.getElementById('review-current-index').textContent = index + 1;
    document.getElementById('review-total-count').textContent = reviewProblems.length;
    document.getElementById('review-student-name').textContent = problemMeta.student_name || 'Unknown';
    document.getElementById('review-current-score').textContent = problemMeta.score;
    document.getElementById('review-current-max').textContent = problemMeta.max_points;

    // Format graded_at timestamp
    const gradedAt = new Date(problemMeta.graded_at);
    document.getElementById('review-graded-at').textContent = gradedAt.toLocaleString();

    // Show/hide blank indicator
    if (problemMeta.is_blank) {
        document.getElementById('review-blank-info').style.display = 'block';
    } else {
        document.getElementById('review-blank-info').style.display = 'none';
    }

    // Fetch full problem data (including image)
    try {
        const response = await fetch(`${API_BASE}/problems/${problemMeta.id}`);
        if (!response.ok) {
            throw new Error('Failed to load problem details');
        }

        const problem = await response.json();

        // Display image
        document.getElementById('review-problem-image').src =
            `data:image/png;base64,${problem.image_data}`;

        // Populate form
        document.getElementById('review-score-input').value = problem.score;
        document.getElementById('review-score-slider').value = problem.score;
        document.getElementById('review-feedback-input').value = problem.feedback || '';

        // Store current problem ID for saving
        document.getElementById('review-save-btn').dataset.problemId = problem.id;
        document.getElementById('review-decipher-btn').dataset.problemId = problem.id;

        // Setup score sync for review inputs
        setupReviewScoreSync();

    } catch (error) {
        console.error('Failed to load problem details:', error);
        alert('Failed to load problem: ' + error.message);
    }
}

// Setup score sync for review inputs
function setupReviewScoreSync() {
    const slider = document.getElementById('review-score-slider');
    const input = document.getElementById('review-score-input');

    // Remove old listeners
    const newSlider = slider.cloneNode(true);
    const newInput = input.cloneNode(true);
    slider.parentNode.replaceChild(newSlider, slider);
    input.parentNode.replaceChild(newInput, input);

    // Add new listeners
    newSlider.addEventListener('input', (e) => {
        newInput.value = e.target.value;
    });

    newInput.addEventListener('input', (e) => {
        const value = parseFloat(e.target.value);
        if (!isNaN(value)) {
            newSlider.value = value;
        }
    });
}

// Previous button
document.getElementById('review-prev-btn').addEventListener('click', () => {
    if (reviewCurrentIndex > 0) {
        loadReviewProblem(reviewCurrentIndex - 1);
    }
});

// Next button
document.getElementById('review-next-btn').addEventListener('click', () => {
    if (reviewCurrentIndex < reviewProblems.length - 1) {
        loadReviewProblem(reviewCurrentIndex + 1);
    }
});

// Save changes button
document.getElementById('review-save-btn').addEventListener('click', async () => {
    const problemId = document.getElementById('review-save-btn').dataset.problemId;
    if (!problemId) return;

    const score = parseFloat(document.getElementById('review-score-input').value);
    const feedback = document.getElementById('review-feedback-input').value;
    const maxPoints = problemMaxPoints[reviewProblemNumber] || 8;

    if (isNaN(score)) {
        alert('Please enter a valid score');
        return;
    }

    if (score > maxPoints) {
        alert(`Score cannot exceed ${maxPoints} points`);
        return;
    }

    // Show loading state
    const saveBtn = document.getElementById('review-save-btn');
    const originalText = saveBtn.textContent;
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving...';

    try {
        const response = await fetch(`${API_BASE}/problems/${problemId}/grade`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ score, feedback })
        });

        if (!response.ok) {
            throw new Error(`Failed to save changes: ${response.statusText}`);
        }

        // Update local cache
        reviewProblems[reviewCurrentIndex].score = score;
        reviewProblems[reviewCurrentIndex].feedback = feedback;

        // Update display
        document.getElementById('review-current-score').textContent = score;

        // Show success feedback
        saveBtn.textContent = 'Saved ✓';
        setTimeout(() => {
            saveBtn.textContent = originalText;
        }, 2000);

    } catch (error) {
        console.error('Failed to save changes:', error);
        alert('Failed to save changes: ' + error.message);
    } finally {
        saveBtn.disabled = false;
    }
});

// Decipher button in review mode
document.getElementById('review-decipher-btn').addEventListener('click', async () => {
    const problemId = document.getElementById('review-decipher-btn').dataset.problemId;
    if (!problemId) return;

    // Show transcription dialog
    const transcriptionText = document.getElementById('transcription-text');
    const transcriptionActions = document.getElementById('transcription-actions');
    const transcriptionDialog = document.getElementById('transcription-dialog');

    transcriptionText.innerHTML = '<div class="transcription-loading">Transcribing handwriting...</div>';
    transcriptionActions.style.display = 'none';
    transcriptionDialog.style.display = 'flex';

    try {
        const transcription = await fetchTranscription(problemId, false);
        displayTranscription(transcription);
    } catch (error) {
        console.error('Failed to decipher handwriting:', error);
        transcriptionText.innerHTML = '<div style="color: var(--danger-color);">Failed to transcribe handwriting. Please try again.</div>';
        transcriptionActions.style.display = 'none';
    }
});

// Keyboard navigation in review mode
document.addEventListener('keydown', (e) => {
    // Only handle when review dialog is visible
    const reviewDialog = document.getElementById('review-dialog');
    if (reviewDialog.style.display !== 'flex') return;

    // Don't handle if typing in textarea
    if (e.target.tagName === 'TEXTAREA') return;

    // Left arrow - previous
    if (e.key === 'ArrowLeft') {
        e.preventDefault();
        if (reviewCurrentIndex > 0) {
            loadReviewProblem(reviewCurrentIndex - 1);
        }
    }

    // Right arrow - next
    if (e.key === 'ArrowRight') {
        e.preventDefault();
        if (reviewCurrentIndex < reviewProblems.length - 1) {
            loadReviewProblem(reviewCurrentIndex + 1);
        }
    }

    // Escape - close
    if (e.key === 'Escape') {
        e.preventDefault();
        reviewDialog.style.display = 'none';
        loadProblemOrMostRecent();
    }

    // Enter - save (when not in textarea)
    if (e.key === 'Enter' && e.target.id !== 'review-feedback-input') {
        e.preventDefault();
        document.getElementById('review-save-btn').click();
    }
});
