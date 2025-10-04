// Grading interface logic

let currentProblem = null;
let currentProblemNumber = 1;
let availableProblemNumbers = [];
let lastGradedProblemNumber = null; // Track if we just graded something

// Initialize grading interface when section becomes active
function initializeGrading() {
    if (!currentSession) return;

    loadProblemNumbers();
    setupGradingControls();
    updateOverallProgress();
}

// Show notification overlay
function showNotification(message, callback) {
    const overlay = document.getElementById('notification-overlay');
    const messageEl = document.getElementById('notification-message');
    const okBtn = document.getElementById('notification-ok');

    messageEl.textContent = message;
    overlay.style.display = 'flex';

    const dismiss = () => {
        overlay.style.display = 'none';
        document.removeEventListener('keydown', handleNotificationKey);
        if (callback) callback();
    };

    const handleNotificationKey = (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            dismiss();
        }
    };

    okBtn.onclick = dismiss;
    document.addEventListener('keydown', handleNotificationKey);

    // Focus the button for accessibility
    okBtn.focus();
}

// Load available problem numbers
async function loadProblemNumbers() {
    try {
        const response = await fetch(`${API_BASE}/sessions/${currentSession.id}/problem-numbers`);
        const data = await response.json();
        availableProblemNumbers = data.problem_numbers;

        const select = document.getElementById('problem-select');
        select.innerHTML = '';

        availableProblemNumbers.forEach(num => {
            const option = document.createElement('option');
            option.value = num;
            option.textContent = `Problem ${num}`;
            select.appendChild(option);
        });

        currentProblemNumber = availableProblemNumbers[0] || 1;
        select.value = currentProblemNumber;
        select.onchange = () => {
            currentProblemNumber = parseInt(select.value);
            loadNextProblem();
        };

        loadNextProblem();
    } catch (error) {
        console.error('Failed to load problem numbers:', error);
    }
}

// Update overall progress display
async function updateOverallProgress() {
    try {
        const response = await fetch(`${API_BASE}/sessions/${currentSession.id}/stats`);
        const stats = await response.json();

        const percentage = stats.progress_percentage || 0;
        document.getElementById('overall-progress-fill').style.width = `${percentage}%`;
        document.getElementById('overall-progress-label').textContent =
            `Overall: ${stats.problems_graded} / ${stats.total_problems} (${percentage.toFixed(1)}%)`;
    } catch (error) {
        console.error('Failed to update overall progress:', error);
    }
}

// Upload more exams button
document.getElementById('upload-more-btn').addEventListener('click', () => {
    if (!currentSession) return;
    // Navigate back to upload section with currentSession still set
    navigateToSection('upload-section');
    // Show a message that we're adding to existing session
    document.getElementById('initial-upload-message').style.display = 'block';
    document.getElementById('initial-upload-message').innerHTML =
        `<strong>Adding exams to:</strong> ${currentSession.assignment_name} - ${currentSession.course_name || `Course ${currentSession.course_id}`}`;
});

// Setup grading controls
function setupGradingControls() {
    document.getElementById('submit-grade-btn').onclick = submitGrade;
    document.getElementById('next-problem-btn').onclick = loadNextProblem;
    document.getElementById('view-stats-btn').onclick = () => {
        navigateToSection('stats-section');
        loadStatistics();
    };

    // Keyboard shortcuts
    document.addEventListener('keydown', handleGradingKeyboard);
}

// Handle keyboard shortcuts for grading
function handleGradingKeyboard(e) {
    // Only handle when grading section is active
    if (!document.getElementById('grading-section').classList.contains('active')) {
        return;
    }

    // Don't handle if typing in textarea
    if (e.target.tagName === 'TEXTAREA') {
        return;
    }

    // Enter key - submit and move to next
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        submitGrade();
    }

    // Shift+Tab - skip to next without grading
    if (e.key === 'Tab' && e.shiftKey) {
        e.preventDefault();
        loadNextProblem();
    }

    // Number keys 0-9 - quick score entry
    if (/^[0-9]$/.test(e.key) && e.target.id !== 'score-input' && e.target.id !== 'feedback-input') {
        e.preventDefault();
        document.getElementById('score-input').value = e.key;
        document.getElementById('score-input').focus();
    }
}

// Load next ungraded problem
async function loadNextProblem() {
    try {
        const response = await fetch(
            `${API_BASE}/problems/${currentSession.id}/${currentProblemNumber}/next`
        );

        if (response.status === 404) {
            // No more problems for this number
            // Only show notification if we just graded something from this problem
            if (lastGradedProblemNumber === currentProblemNumber) {
                lastGradedProblemNumber = null; // Reset

                // Find next ungraded problem number
                const currentIndex = availableProblemNumbers.indexOf(currentProblemNumber);
                const nextProblemNumber = availableProblemNumbers[currentIndex + 1];

                if (nextProblemNumber) {
                    showNotification(`All submissions for Problem ${currentProblemNumber} are graded! Moving to Problem ${nextProblemNumber}...`, () => {
                        currentProblemNumber = nextProblemNumber;
                        document.getElementById('problem-select').value = currentProblemNumber;
                        loadNextProblem();
                    });
                } else {
                    // All done!
                    showNotification('All problems are graded! 🎉', () => {
                        navigateToSection('stats-section');
                        loadStatistics();
                    });
                }
            } else {
                // Already complete, just silently move to next or stats
                const currentIndex = availableProblemNumbers.indexOf(currentProblemNumber);
                const nextProblemNumber = availableProblemNumbers[currentIndex + 1];

                if (nextProblemNumber) {
                    currentProblemNumber = nextProblemNumber;
                    document.getElementById('problem-select').value = currentProblemNumber;
                    loadNextProblem();
                } else {
                    // Show stats if everything is done
                    navigateToSection('stats-section');
                    loadStatistics();
                }
            }
            return;
        }

        currentProblem = await response.json();

        // Display problem
        document.getElementById('problem-image').src =
            `data:image/png;base64,${currentProblem.image_data}`;

        // Update progress
        document.getElementById('grading-progress').textContent =
            `${currentProblem.current_index} / ${currentProblem.total_count}`;

        // Clear/populate form based on blank detection
        if (currentProblem.is_blank) {
            // Auto-populate score as 0 for detected blank problems
            document.getElementById('score-input').value = '0';
            document.getElementById('feedback-input').value = 'No answer provided';

            // Show blank detection indicator
            const blankIndicator = document.createElement('div');
            blankIndicator.id = 'blank-indicator';
            blankIndicator.className = 'blank-indicator';
            blankIndicator.innerHTML = `
                <strong>⚠️ Blank Detected</strong>
                <div style="font-size: 12px; margin-top: 5px;">
                    Confidence: ${(currentProblem.blank_confidence * 100).toFixed(0)}%
                    (${currentProblem.blank_method || 'heuristic'})
                </div>
            `;

            // Remove old indicator if exists
            const oldIndicator = document.getElementById('blank-indicator');
            if (oldIndicator) oldIndicator.remove();

            // Insert before the problem image
            const problemContainer = document.querySelector('.problem-container');
            problemContainer.parentNode.insertBefore(blankIndicator, problemContainer);
        } else {
            // Remove blank indicator if it exists
            const oldIndicator = document.getElementById('blank-indicator');
            if (oldIndicator) oldIndicator.remove();

            // Clear form for non-blank problems
            document.getElementById('score-input').value = '';
            document.getElementById('feedback-input').value = '';
        }

    } catch (error) {
        console.error('Failed to load problem:', error);
        alert('Failed to load problem');
    }
}

// Submit grade for current problem
async function submitGrade() {
    if (!currentProblem) return;

    const score = parseFloat(document.getElementById('score-input').value);
    const feedback = document.getElementById('feedback-input').value;

    if (isNaN(score)) {
        alert('Please enter a valid score');
        return;
    }

    // Show loading state
    const submitBtn = document.getElementById('submit-grade-btn');
    const originalText = submitBtn.textContent;
    submitBtn.disabled = true;
    submitBtn.textContent = 'Submitting...';

    try {
        const response = await fetch(`${API_BASE}/problems/${currentProblem.id}/grade`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ score, feedback })
        });

        if (!response.ok) {
            throw new Error(`Failed to submit grade: ${response.statusText}`);
        }

        // Mark that we just graded this problem number
        lastGradedProblemNumber = currentProblemNumber;

        // Update overall progress
        await updateOverallProgress();

        // Load next problem
        await loadNextProblem();

        // Restore button state after loading next problem
        submitBtn.disabled = false;
        submitBtn.textContent = originalText;
    } catch (error) {
        console.error('Failed to submit grade:', error);
        alert('Failed to submit grade: ' + error.message);

        // Restore button state on error
        submitBtn.disabled = false;
        submitBtn.textContent = originalText;
    }
}

// Load statistics
async function loadStatistics() {
    try {
        const [statsResponse, scoresResponse] = await Promise.all([
            fetch(`${API_BASE}/sessions/${currentSession.id}/stats`),
            fetch(`${API_BASE}/sessions/${currentSession.id}/student-scores`)
        ]);

        const stats = await statsResponse.json();
        const scoresData = await scoresResponse.json();

        const container = document.getElementById('stats-container');
        container.innerHTML = `
            <h3>Overall Progress</h3>
            <div class="overall-stats">
                <div class="stat-card">
                    <h3>Total Submissions</h3>
                    <div class="value">${stats.total_submissions}</div>
                </div>
                <div class="stat-card">
                    <h3>Problems Graded</h3>
                    <div class="value">${stats.problems_graded} / ${stats.total_problems}</div>
                </div>
                <div class="stat-card">
                    <h3>Overall Progress</h3>
                    <div class="value">${stats.progress_percentage.toFixed(1)}%</div>
                    <div class="progress-bar-container">
                        <div class="progress-bar-fill" style="width: ${stats.progress_percentage}%"></div>
                    </div>
                </div>
            </div>
        `;

        // Add per-problem stats
        if (stats.problem_stats.length > 0) {
            container.innerHTML += '<h3 style="margin-top: 30px;">Per-Problem Statistics</h3>';
            const problemStatsHtml = stats.problem_stats.map(ps => {
                const problemProgress = ps.num_total > 0 ? (ps.num_graded / ps.num_total * 100) : 0;
                return `
                    <div class="stat-card">
                        <h3>Problem ${ps.problem_number}</h3>
                        <div>Average: ${ps.avg_score ? ps.avg_score.toFixed(2) : 'N/A'}</div>
                        <div>Graded: ${ps.num_graded} / ${ps.num_total} (${problemProgress.toFixed(0)}%)</div>
                    </div>
                `;
            }).join('');
            container.innerHTML += '<div class="problem-stats-grid">' + problemStatsHtml + '</div>';
        }

        // Add student scores table
        if (scoresData.students.length > 0) {
            container.innerHTML += '<h3 style="margin-top: 30px;">Student Scores</h3>';
            const studentScoresHtml = `
                <table class="student-scores-table">
                    <thead>
                        <tr>
                            <th>Student Name</th>
                            <th>Progress</th>
                            <th>Total Score</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${scoresData.students.map(s => `
                            <tr class="${s.is_complete ? 'complete' : 'incomplete'}">
                                <td>${s.student_name || 'Unmatched'}</td>
                                <td>${s.graded_problems} / ${s.total_problems}</td>
                                <td>${s.total_score ? s.total_score.toFixed(2) : '0.00'}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
            container.innerHTML += studentScoresHtml;
        }
    } catch (error) {
        console.error('Failed to load statistics:', error);
    }
}

// Finalize and upload to Canvas
document.getElementById('finalize-btn').onclick = async () => {
    if (!currentSession) return;

    // Check if all grading is complete
    try {
        const [statsResponse, canvasInfoResponse] = await Promise.all([
            fetch(`${API_BASE}/sessions/${currentSession.id}/stats`),
            fetch(`${API_BASE}/sessions/${currentSession.id}/canvas-info`)
        ]);

        const stats = await statsResponse.json();
        const canvasInfo = await canvasInfoResponse.json();

        if (stats.problems_graded < stats.total_problems) {
            showNotification(
                `Cannot finalize: ${stats.total_problems - stats.problems_graded} problems still ungraded. Please complete all grading first.`
            );
            return;
        }

        // Confirm finalization with Canvas details
        const confirmMessage = `Ready to finalize and upload ${stats.total_submissions} submissions to Canvas?\n\n` +
            `Canvas Details:\n` +
            `- Environment: ${canvasInfo.environment.toUpperCase()}\n` +
            `- Course: ${canvasInfo.course_name}\n` +
            `- Assignment: ${canvasInfo.assignment_name}\n` +
            `- URL: ${canvasInfo.canvas_url}\n\n` +
            `This will:\n` +
            `- Generate annotated PDFs with scores\n` +
            `- Upload to Canvas with detailed comments\n` +
            `- Mark this session as complete`;

        if (!confirm(confirmMessage)) {
            return;
        }

        // Start finalization
        const response = await fetch(`${API_BASE}/finalize/${currentSession.id}/finalize`, {
            method: 'POST'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Finalization failed');
        }

        // Show progress area and start polling
        const progressDiv = document.getElementById('finalization-progress');
        const messageDiv = document.getElementById('finalization-message');
        const progressBar = document.getElementById('finalization-progress-bar');

        progressDiv.style.display = 'block';
        messageDiv.textContent = 'Starting finalization...';
        progressBar.style.width = '0%';
        document.getElementById('finalize-btn').disabled = true;

        startFinalizationPolling();

    } catch (error) {
        console.error('Finalization failed:', error);
        alert('Failed to start finalization: ' + error.message);
    }
};

// Poll for finalization status
function startFinalizationPolling() {
    const interval = setInterval(async () => {
        try {
            const response = await fetch(`${API_BASE}/finalize/${currentSession.id}/finalization-status`);
            const status = await response.json();

            // Update UI with progress message
            if (status.message) {
                document.getElementById('finalization-message').textContent = status.message;

                // Try to parse progress from message (format: "Processing X/Y: ...")
                const progressMatch = status.message.match(/Processing (\d+)\/(\d+)/);
                if (progressMatch) {
                    const current = parseInt(progressMatch[1]);
                    const total = parseInt(progressMatch[2]);
                    const percentage = (current / total) * 100;
                    document.getElementById('finalization-progress-bar').style.width = `${percentage}%`;
                }
            }

            // Check if complete
            if (status.status === 'complete') {
                clearInterval(interval);
                document.getElementById('finalization-progress-bar').style.width = '100%';
                showNotification('Finalization complete! All grades have been uploaded to Canvas. 🎉', () => {
                    // Reload session to update status
                    location.reload();
                });
            } else if (status.status === 'error') {
                clearInterval(interval);
                document.getElementById('finalization-progress').style.backgroundColor = '#fee2e2';
                showNotification('Finalization failed: ' + status.message);
            }

        } catch (error) {
            console.error('Failed to check finalization status:', error);
        }
    }, 500);  // Poll every 500ms for responsive updates
}

// Handwriting Transcription Dialog
const transcriptionDialog = document.getElementById('transcription-dialog');
const transcriptionText = document.getElementById('transcription-text');
const transcriptionActions = document.getElementById('transcription-actions');
const modelUsed = document.getElementById('model-used');
const closeTranscription = document.getElementById('close-transcription');
const decipherBtn = document.getElementById('decipher-btn');
const retryPremiumBtn = document.getElementById('retry-premium-btn');

// Cache for transcriptions: { problemId: { standard: {text, model}, premium: {text, model} } }
const transcriptionCache = {};

// Make dialog draggable
let isDragging = false;
let dragOffsetX = 0;
let dragOffsetY = 0;

document.querySelector('.transcription-header').addEventListener('mousedown', (e) => {
    if (e.target.classList.contains('transcription-close')) return;
    isDragging = true;
    const rect = transcriptionDialog.getBoundingClientRect();
    dragOffsetX = e.clientX - rect.left;
    dragOffsetY = e.clientY - rect.top;
    transcriptionDialog.style.transform = 'none';
});

document.addEventListener('mousemove', (e) => {
    if (!isDragging) return;
    transcriptionDialog.style.left = (e.clientX - dragOffsetX) + 'px';
    transcriptionDialog.style.top = (e.clientY - dragOffsetY) + 'px';
});

document.addEventListener('mouseup', () => {
    isDragging = false;
});

// Close dialog
closeTranscription.addEventListener('click', () => {
    transcriptionDialog.style.display = 'none';
});

// Function to fetch transcription (with caching)
async function fetchTranscription(problemId, usePremium = false) {
    const cacheKey = usePremium ? 'premium' : 'standard';

    // Check cache first
    if (transcriptionCache[problemId] && transcriptionCache[problemId][cacheKey]) {
        console.log(`Using cached ${cacheKey} transcription for problem ${problemId}`);
        return transcriptionCache[problemId][cacheKey];
    }

    // Fetch from API
    const url = `${API_BASE}/problems/${problemId}/decipher?use_premium_model=${usePremium}`;
    const response = await fetch(url, { method: 'POST' });

    if (!response.ok) {
        throw new Error('Transcription failed');
    }

    const data = await response.json();

    // Cache the result
    if (!transcriptionCache[problemId]) {
        transcriptionCache[problemId] = {};
    }
    transcriptionCache[problemId][cacheKey] = {
        text: data.transcription,
        model: data.model
    };

    return transcriptionCache[problemId][cacheKey];
}

// Function to display transcription in dialog
function displayTranscription(transcription) {
    transcriptionText.textContent = transcription.text;
    modelUsed.textContent = `Model used: ${transcription.model}`;
    transcriptionActions.style.display = 'block';
}

// Decipher handwriting button (standard model)
decipherBtn.addEventListener('click', async () => {
    if (!currentProblem) {
        alert('No problem loaded');
        return;
    }

    // Show dialog with loading state
    transcriptionText.innerHTML = '<div class="transcription-loading">Transcribing handwriting...</div>';
    transcriptionActions.style.display = 'none';
    transcriptionDialog.style.display = 'flex';

    try {
        const transcription = await fetchTranscription(currentProblem.id, false);
        displayTranscription(transcription);
    } catch (error) {
        console.error('Failed to decipher handwriting:', error);
        transcriptionText.innerHTML = '<div style="color: var(--danger-color);">Failed to transcribe handwriting. Please try again.</div>';
        transcriptionActions.style.display = 'none';
    }
});

// Retry with premium model button
retryPremiumBtn.addEventListener('click', async () => {
    if (!currentProblem) return;

    // Show loading state
    transcriptionText.innerHTML = '<div class="transcription-loading">Transcribing with better model (Opus)...</div>';
    transcriptionActions.style.display = 'none';

    try {
        const transcription = await fetchTranscription(currentProblem.id, true);
        displayTranscription(transcription);
    } catch (error) {
        console.error('Failed to decipher with premium model:', error);
        transcriptionText.innerHTML = '<div style="color: var(--danger-color);">Failed to transcribe with premium model. Please try again.</div>';
    }
});
