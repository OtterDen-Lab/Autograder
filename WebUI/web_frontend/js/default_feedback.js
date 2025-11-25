// Default Feedback Functionality
// Allows setting default feedback that auto-applies to low-scoring submissions

let currentDefaultFeedback = {
    text: null,
    threshold: 100.0,
    applyOnZero: true,
    applyOnBlank: true,
    applyOnThreshold: false
};

// =============================================================================
// LOAD AND DISPLAY DEFAULT FEEDBACK
// =============================================================================

async function loadDefaultFeedback(sessionId, problemNumber) {
    try {
        const response = await fetch(`${API_BASE}/sessions/${sessionId}/default-feedback/${problemNumber}`);

        if (!response.ok) {
            throw new Error('Failed to load default feedback');
        }

        const data = await response.json();

        currentDefaultFeedback.text = data.default_feedback;
        currentDefaultFeedback.threshold = data.default_feedback_threshold || 100.0;

        // Load apply conditions from localStorage (per session/problem)
        const storageKey = `defaultFeedback_${sessionId}_${problemNumber}`;
        const savedConditions = localStorage.getItem(storageKey);
        if (savedConditions) {
            const conditions = JSON.parse(savedConditions);
            currentDefaultFeedback.applyOnZero = conditions.applyOnZero !== false;
            currentDefaultFeedback.applyOnBlank = conditions.applyOnBlank !== false;
            currentDefaultFeedback.applyOnThreshold = conditions.applyOnThreshold || false;
        }

        displayDefaultFeedback();

        // Show container if we have default feedback
        const container = document.getElementById('default-feedback-container');
        if (container) {
            container.style.display = 'block';
        }

    } catch (error) {
        console.error('Failed to load default feedback:', error);
        currentDefaultFeedback.text = null;
        displayDefaultFeedback();
    }
}

function displayDefaultFeedback() {
    const display = document.getElementById('default-feedback-display');
    if (!display) return;

    display.innerHTML = '';

    if (!currentDefaultFeedback.text) {
        const placeholder = document.createElement('span');
        placeholder.className = 'default-feedback-placeholder';
        placeholder.textContent = 'No default feedback set.';
        display.appendChild(placeholder);
    } else {
        display.textContent = currentDefaultFeedback.text;
    }
}

// =============================================================================
// EDIT DEFAULT FEEDBACK
// =============================================================================

function showEditDefaultFeedbackDialog() {
    const dialog = document.getElementById('edit-default-feedback-dialog');
    if (!dialog) return;

    // Populate current values
    document.getElementById('default-feedback-text').value = currentDefaultFeedback.text || '';
    document.getElementById('threshold-percentage').value = currentDefaultFeedback.threshold || 100.0;
    document.getElementById('apply-on-zero').checked = currentDefaultFeedback.applyOnZero;
    document.getElementById('apply-on-blank').checked = currentDefaultFeedback.applyOnBlank;
    document.getElementById('apply-on-threshold').checked = currentDefaultFeedback.applyOnThreshold;

    dialog.style.display = 'flex';
    document.getElementById('default-feedback-text').focus();
}

function hideEditDefaultFeedbackDialog() {
    const dialog = document.getElementById('edit-default-feedback-dialog');
    if (dialog) {
        dialog.style.display = 'none';
    }
}

function clearDefaultFeedback() {
    if (!confirm('Clear default feedback for this problem?\n\nThis will remove the default feedback text.')) {
        return;
    }

    document.getElementById('default-feedback-text').value = '';
    document.getElementById('apply-on-zero').checked = true;
    document.getElementById('apply-on-blank').checked = true;
    document.getElementById('apply-on-threshold').checked = false;
    document.getElementById('threshold-percentage').value = 100.0;
}

async function saveDefaultFeedback() {
    const textArea = document.getElementById('default-feedback-text');
    const thresholdInput = document.getElementById('threshold-percentage');
    const applyOnZero = document.getElementById('apply-on-zero').checked;
    const applyOnBlank = document.getElementById('apply-on-blank').checked;
    const applyOnThreshold = document.getElementById('apply-on-threshold').checked;

    if (!textArea || !thresholdInput) return;

    const feedbackText = textArea.value.trim();
    const threshold = parseFloat(thresholdInput.value) || 100.0;

    // Validate
    if (feedbackText && feedbackText.length > 2000) {
        alert('Default feedback must be 2000 characters or less');
        textArea.focus();
        return;
    }

    if (threshold < 0 || threshold > 100) {
        alert('Threshold must be between 0 and 100');
        thresholdInput.focus();
        return;
    }

    // Save to backend
    try {
        const saveBtn = document.getElementById('save-default-feedback-btn');
        saveBtn.disabled = true;
        saveBtn.textContent = 'Saving...';

        const params = new URLSearchParams({
            problem_number: currentProblemNumber,
            threshold: threshold
        });

        if (feedbackText) {
            params.append('default_feedback', feedbackText);
        }

        const response = await fetch(`${API_BASE}/sessions/${currentSession.id}/default-feedback?${params}`, {
            method: 'PUT'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to save default feedback');
        }

        // Save apply conditions to localStorage
        const storageKey = `defaultFeedback_${currentSession.id}_${currentProblemNumber}`;
        localStorage.setItem(storageKey, JSON.stringify({
            applyOnZero,
            applyOnBlank,
            applyOnThreshold
        }));

        // Update current state
        currentDefaultFeedback.text = feedbackText || null;
        currentDefaultFeedback.threshold = threshold;
        currentDefaultFeedback.applyOnZero = applyOnZero;
        currentDefaultFeedback.applyOnBlank = applyOnBlank;
        currentDefaultFeedback.applyOnThreshold = applyOnThreshold;

        // Update display
        displayDefaultFeedback();

        // Close dialog
        hideEditDefaultFeedbackDialog();

    } catch (error) {
        console.error('Failed to save default feedback:', error);
        alert(`Failed to save default feedback: ${error.message}`);
    } finally {
        const saveBtn = document.getElementById('save-default-feedback-btn');
        saveBtn.disabled = false;
        saveBtn.textContent = 'Save';
    }
}

// =============================================================================
// AUTO-APPLY LOGIC
// =============================================================================

function shouldApplyDefaultFeedback(score, isBlank) {
    // No default feedback set
    if (!currentDefaultFeedback.text) {
        return false;
    }

    // Check conditions
    if (currentDefaultFeedback.applyOnBlank && isBlank) {
        return true;
    }

    if (currentDefaultFeedback.applyOnZero && score === 0) {
        return true;
    }

    if (currentDefaultFeedback.applyOnThreshold) {
        // Calculate percentage based on max_points
        const maxPoints = currentProblem?.max_points || 8.0;
        const percentage = (score / maxPoints) * 100;
        if (percentage <= currentDefaultFeedback.threshold) {
            return true;
        }
    }

    return false;
}

function applyDefaultFeedbackToTextarea() {
    const feedbackInput = document.getElementById('feedback-input');
    if (!feedbackInput || !currentDefaultFeedback.text) return;

    const currentFeedback = feedbackInput.value.trim();

    // Don't apply if already present
    if (currentFeedback.includes(currentDefaultFeedback.text)) {
        return;
    }

    // Append default feedback
    if (currentFeedback) {
        feedbackInput.value = `${currentFeedback}\n\n${currentDefaultFeedback.text}`;
    } else {
        feedbackInput.value = currentDefaultFeedback.text;
    }
}

// =============================================================================
// DRAGGABLE DIALOG
// =============================================================================

let isDefaultFeedbackDragging = false;
let defaultFeedbackDragOffsetX = 0;
let defaultFeedbackDragOffsetY = 0;

// Setup draggable dialog
function setupDefaultFeedbackDragging() {
    const defaultFeedbackDialog = document.getElementById('edit-default-feedback-dialog');
    const dialogContent = defaultFeedbackDialog?.querySelector('.dialog-content');
    const defaultFeedbackHeader = defaultFeedbackDialog?.querySelector('.dialog-header');

    if (!defaultFeedbackHeader || !dialogContent) return;

    defaultFeedbackHeader.addEventListener('mousedown', (e) => {
        // Don't drag if clicking on buttons
        if (e.target.tagName === 'BUTTON') return;

        isDefaultFeedbackDragging = true;
        const rect = dialogContent.getBoundingClientRect();
        defaultFeedbackDragOffsetX = e.clientX - rect.left;
        defaultFeedbackDragOffsetY = e.clientY - rect.top;
        dialogContent.style.position = 'fixed';
        dialogContent.style.margin = '0';
    });

    document.addEventListener('mousemove', (e) => {
        if (!isDefaultFeedbackDragging || !dialogContent) return;
        dialogContent.style.left = (e.clientX - defaultFeedbackDragOffsetX) + 'px';
        dialogContent.style.top = (e.clientY - defaultFeedbackDragOffsetY) + 'px';
    });

    document.addEventListener('mouseup', () => {
        isDefaultFeedbackDragging = false;
    });
}

// Initialize dragging when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupDefaultFeedbackDragging);
} else {
    setupDefaultFeedbackDragging();
}

// =============================================================================
// EVENT LISTENERS
// =============================================================================

document.getElementById('edit-default-feedback-btn')?.addEventListener('click', showEditDefaultFeedbackDialog);
document.getElementById('cancel-default-feedback-btn')?.addEventListener('click', hideEditDefaultFeedbackDialog);
document.getElementById('clear-default-feedback-btn')?.addEventListener('click', clearDefaultFeedback);
document.getElementById('save-default-feedback-btn')?.addEventListener('click', saveDefaultFeedback);

// Click outside dialog to close
document.getElementById('edit-default-feedback-dialog')?.addEventListener('click', (e) => {
    if (e.target.id === 'edit-default-feedback-dialog') {
        hideEditDefaultFeedbackDialog();
    }
});

// Ctrl+Enter to save
document.getElementById('default-feedback-text')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.ctrlKey) {
        e.preventDefault();
        saveDefaultFeedback();
    }
});