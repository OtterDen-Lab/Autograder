// Grading interface logic

let currentProblem = null;
let currentProblemNumber = 1;
let availableProblemNumbers = [];
let lastGradedProblemNumber = null; // Track if we just graded something
let problemMaxPoints = {}; // Cache max points per problem number
let problemHistory = []; // Track navigation history for back button
let historyIndex = -1; // Current position in history

// Initialize grading interface when section becomes active
function initializeGrading() {
    if (!currentSession) return;

    loadProblemMaxPoints();
    loadProblemNumbers();
    setupGradingControls();
    updateOverallProgress();
}

// Load max points metadata for all problems
async function loadProblemMaxPoints() {
    try {
        const response = await fetch(`${API_BASE}/sessions/${currentSession.id}/problem-max-points-all`);
        const data = await response.json();
        problemMaxPoints = data.max_points || {};
    } catch (error) {
        console.error('Failed to load max points metadata:', error);
        problemMaxPoints = {};
    }
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

// Update max points dropdown based on current problem number
function updateMaxPointsDropdown() {
    const maxPointsInput = document.getElementById('max-points-input');
    const scoreInput = document.getElementById('score-input');
    const scoreSlider = document.getElementById('score-slider');
    const cachedMax = problemMaxPoints[currentProblemNumber];

    // Default to 8 if not set
    const maxPoints = cachedMax || 8;

    maxPointsInput.value = maxPoints;
    scoreSlider.max = maxPoints;
    scoreInput.max = maxPoints;
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
        select.onchange = async () => {
            currentProblemNumber = parseInt(select.value);
            updateMaxPointsDropdown();
            await loadProblemOrMostRecent();
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

// Setup score sync between slider and input
function setupScoreSync() {
    const scoreSlider = document.getElementById('score-slider');
    const scoreInput = document.getElementById('score-input');

    // Remove old listeners by replacing elements
    const newSlider = scoreSlider.cloneNode(true);
    const newInput = scoreInput.cloneNode(true);
    scoreSlider.parentNode.replaceChild(newSlider, scoreSlider);
    scoreInput.parentNode.replaceChild(newInput, scoreInput);

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

// Setup grading controls
function setupGradingControls() {
    document.getElementById('submit-grade-btn').onclick = submitGrade;
    document.getElementById('next-problem-btn').onclick = loadNextProblem;
    document.getElementById('back-problem-btn').onclick = loadPreviousProblem;
    document.getElementById('view-stats-btn').onclick = () => {
        navigateToSection('stats-section');
        loadStatistics();
    };

    // Continue grading button (in stats section)
    document.getElementById('continue-grading-btn').onclick = () => {
        navigateToSection('grading-section');
    };

    // Initial score sync setup
    setupScoreSync();

    // Max points input handler
    const maxPointsInput = document.getElementById('max-points-input');
    maxPointsInput.addEventListener('change', async (e) => {
        const maxPoints = parseFloat(e.target.value);
        if (!isNaN(maxPoints) && maxPoints > 0 && currentProblemNumber) {
            // Update slider and input max
            document.getElementById('score-slider').max = maxPoints;
            document.getElementById('score-input').max = maxPoints;

            // Save to cache
            problemMaxPoints[currentProblemNumber] = maxPoints;

            // Save to backend
            try {
                const response = await fetch(`${API_BASE}/sessions/${currentSession.id}/problem-max-points?problem_number=${currentProblemNumber}&max_points=${maxPoints}`, {
                    method: 'PUT'
                });

                if (!response.ok) {
                    throw new Error('Failed to save max points');
                }

                // Update current problem object
                if (currentProblem) {
                    currentProblem.max_points = maxPoints;
                }
            } catch (error) {
                console.error('Failed to save max points:', error);
                alert('Failed to save max points: ' + error.message);
            }
        }
    });

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
    if (/^[0-9]$/.test(e.key) && e.target.id !== 'score-input' && e.target.id !== 'feedback-input' && e.target.id !== 'max-points-input') {
        e.preventDefault();
        document.getElementById('score-input').value = e.key;
        document.getElementById('score-input').focus();
    }
}

// Display the current problem (common display logic)
function displayCurrentProblem() {
    if (!currentProblem) return;

    // Display problem
    document.getElementById('problem-image').src =
        `data:image/png;base64,${currentProblem.image_data}`;

    // Update progress with blank count
    let progressText = `${currentProblem.current_index} / ${currentProblem.total_count}`;

    // Add blank info if there are ungraded blanks
    if (currentProblem.ungraded_blank > 0 || currentProblem.ungraded_nonblank > 0) {
        const remaining = currentProblem.ungraded_blank + currentProblem.ungraded_nonblank;
        if (currentProblem.ungraded_blank > 0) {
            progressText += ` (${currentProblem.ungraded_blank} blank)`;
        }
    }

    document.getElementById('grading-progress').textContent = progressText;

    // Update max points from cache
    updateMaxPointsDropdown();

    // Re-attach event listeners
    setupScoreSync();

    // Populate form based on whether it's graded or blank
    if (currentProblem.graded) {
        // Already graded - show existing grade
        document.getElementById('score-input').value = currentProblem.score || '';
        document.getElementById('score-slider').value = currentProblem.score || 0;
        document.getElementById('feedback-input').value = currentProblem.feedback || '';

        // Remove blank indicator
        const oldIndicator = document.getElementById('blank-indicator');
        if (oldIndicator) oldIndicator.remove();
    } else if (currentProblem.is_blank) {
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
        // Clear form for non-blank problems
        document.getElementById('score-input').value = '';
        document.getElementById('feedback-input').value = '';

        // Remove blank indicator if it exists
        const oldIndicator = document.getElementById('blank-indicator');
        if (oldIndicator) oldIndicator.remove();
    }
}

// Load problem for current problem number (ungraded if available, otherwise most recent)
async function loadProblemOrMostRecent() {
    try {
        // Try to load next ungraded problem first
        const nextResponse = await fetch(
            `${API_BASE}/problems/${currentSession.id}/${currentProblemNumber}/next`
        );

        if (nextResponse.ok) {
            // Found an ungraded problem, load it directly
            currentProblem = await nextResponse.json();
            addToHistory(currentProblem);
            displayCurrentProblem();
        } else if (nextResponse.status === 404) {
            // No ungraded problems, load most recently graded
            const prevResponse = await fetch(
                `${API_BASE}/problems/${currentSession.id}/${currentProblemNumber}/previous`
            );

            if (prevResponse.ok) {
                currentProblem = await prevResponse.json();
                addToHistory(currentProblem);
                displayCurrentProblem();
            } else {
                alert('No problems found for this problem number');
            }
        }
    } catch (error) {
        console.error('Failed to load problem:', error);
        alert('Failed to load problem: ' + error.message);
    }
}

// Add problem to history
function addToHistory(problem) {
    // If we're in the middle of history, remove everything after current position
    if (historyIndex < problemHistory.length - 1) {
        problemHistory = problemHistory.slice(0, historyIndex + 1);
    }

    // Add new problem to history
    problemHistory.push(problem);
    historyIndex = problemHistory.length - 1;

    // Limit history to last 50 problems to avoid memory issues
    if (problemHistory.length > 50) {
        problemHistory.shift();
        historyIndex--;
    }
}

// Load previous problem from history
async function loadPreviousProblem() {
    if (historyIndex > 0) {
        // Go back in history
        historyIndex--;
        currentProblem = problemHistory[historyIndex];
        displayCurrentProblem();
    } else {
        alert('No more previous problems in history');
    }
}

// Find next problem number with ungraded submissions
async function findNextUngradedProblem() {
    // Check each problem number to see if it has ungraded submissions
    for (const problemNum of availableProblemNumbers) {
        try {
            const response = await fetch(
                `${API_BASE}/problems/${currentSession.id}/${problemNum}/next`
            );
            if (response.ok) {
                return problemNum; // Found an ungraded problem
            }
        } catch (error) {
            console.error(`Error checking problem ${problemNum}:`, error);
        }
    }
    return null; // No ungraded problems found
}

// Load next ungraded problem
async function loadNextProblem() {
    try {
        const response = await fetch(
            `${API_BASE}/problems/${currentSession.id}/${currentProblemNumber}/next`
        );

        if (response.status === 404) {
            // No more problems for this number
            // Find next ungraded problem number across all problems
            const nextUngradedProblem = await findNextUngradedProblem();

            if (nextUngradedProblem !== null) {
                // Found ungraded problems in another problem number
                if (lastGradedProblemNumber === currentProblemNumber) {
                    // Show notification if we just graded something
                    lastGradedProblemNumber = null;
                    showNotification(`All submissions for Problem ${currentProblemNumber} are graded! Moving to Problem ${nextUngradedProblem}...`, () => {
                        currentProblemNumber = nextUngradedProblem;
                        document.getElementById('problem-select').value = currentProblemNumber;
                        updateMaxPointsDropdown();
                        loadNextProblem();
                    });
                } else {
                    // Silently move to next ungraded problem
                    currentProblemNumber = nextUngradedProblem;
                    document.getElementById('problem-select').value = currentProblemNumber;
                    updateMaxPointsDropdown();
                    loadNextProblem();
                }
            } else {
                // All problems are truly graded!
                if (lastGradedProblemNumber === currentProblemNumber) {
                    lastGradedProblemNumber = null;
                    showNotification('All problems are graded! 🎉', () => {
                        navigateToSection('stats-section');
                        loadStatistics();
                    });
                } else {
                    // Already complete, go to stats
                    navigateToSection('stats-section');
                    loadStatistics();
                }
            }
            return;
        }

        currentProblem = await response.json();

        // Add to history and display
        addToHistory(currentProblem);
        displayCurrentProblem();

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
    const maxPoints = problemMaxPoints[currentProblemNumber] || 8;

    if (isNaN(score)) {
        alert('Please enter a valid score');
        return;
    }

    if (score > maxPoints) {
        alert(`Score cannot exceed ${maxPoints} points`);
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
                const avgText = ps.avg_score ? ps.avg_score.toFixed(2) : 'N/A';
                const minText = ps.min_score !== null && ps.min_score !== undefined ? ps.min_score.toFixed(2) : 'N/A';
                const maxText = ps.max_score !== null && ps.max_score !== undefined ? ps.max_score.toFixed(2) : 'N/A';
                return `
                    <div class="stat-card">
                        <h3>Problem ${ps.problem_number}</h3>
                        <div>Avg: ${avgText} | Min: ${minText} | Max: ${maxText}</div>
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

// Change Canvas Target button
document.getElementById('change-canvas-target-btn').onclick = async () => {
    if (!currentSession) return;

    const dialog = document.getElementById('canvas-target-dialog');
    const envSelect = document.getElementById('canvas-env-select');
    const courseSelect = document.getElementById('canvas-course-select');
    const assignmentSelect = document.getElementById('canvas-assignment-select');

    // Show dialog
    dialog.style.display = 'flex';

    // Load current settings
    try {
        const response = await fetch(`${API_BASE}/sessions/${currentSession.id}/canvas-info`);
        const info = await response.json();

        // Set current environment
        envSelect.value = info.environment === 'production' ? 'true' : 'false';

        // Load courses for selected environment
        await loadCanvasConfigCourses();

        // Select current course
        courseSelect.value = info.course_id;

        // Load and select current assignment
        await loadCanvasConfigAssignments(info.course_id);
        assignmentSelect.value = info.assignment_id;

    } catch (error) {
        console.error('Failed to load current Canvas config:', error);
    }
};

// Load courses for Canvas config dialog
async function loadCanvasConfigCourses() {
    const envSelect = document.getElementById('canvas-env-select');
    const courseSelect = document.getElementById('canvas-course-select');
    const useProd = envSelect.value === 'true';

    courseSelect.innerHTML = '<option value="">Loading courses...</option>';
    courseSelect.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/canvas/courses?use_prod=${useProd}`);
        const data = await response.json();

        courseSelect.innerHTML = '<option value="">-- Select a Course --</option>';
        data.courses.forEach(course => {
            const option = document.createElement('option');
            option.value = course.id;
            const prefix = course.is_favorite ? '⭐ ' : '';
            option.textContent = prefix + course.name;
            courseSelect.appendChild(option);
        });

        courseSelect.disabled = false;
    } catch (error) {
        console.error('Failed to load courses:', error);
        courseSelect.innerHTML = '<option value="">Failed to load courses</option>';
    }
}

// Load assignments for Canvas config dialog
async function loadCanvasConfigAssignments(courseId) {
    const envSelect = document.getElementById('canvas-env-select');
    const assignmentSelect = document.getElementById('canvas-assignment-select');
    const useProd = envSelect.value === 'true';

    assignmentSelect.innerHTML = '<option value="">Loading assignments...</option>';
    assignmentSelect.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/canvas/courses/${courseId}/assignments?use_prod=${useProd}`);
        const data = await response.json();

        assignmentSelect.innerHTML = '<option value="">-- Select an Assignment --</option>';
        data.assignments.forEach(assignment => {
            const option = document.createElement('option');
            option.value = assignment.id;
            option.textContent = assignment.name;
            assignmentSelect.appendChild(option);
        });

        assignmentSelect.disabled = false;
    } catch (error) {
        console.error('Failed to load assignments:', error);
        assignmentSelect.innerHTML = '<option value="">Failed to load assignments</option>';
    }
}

// Canvas config dialog event handlers
document.getElementById('canvas-env-select').onchange = loadCanvasConfigCourses;
document.getElementById('canvas-course-select').onchange = (e) => {
    if (e.target.value) {
        loadCanvasConfigAssignments(e.target.value);
    }
};

document.getElementById('cancel-canvas-target-btn').onclick = () => {
    document.getElementById('canvas-target-dialog').style.display = 'none';
};

document.getElementById('save-canvas-target-btn').onclick = async () => {
    const courseId = document.getElementById('canvas-course-select').value;
    const assignmentId = document.getElementById('canvas-assignment-select').value;
    const useProd = document.getElementById('canvas-env-select').value === 'true';

    if (!courseId || !assignmentId) {
        alert('Please select both a course and an assignment');
        return;
    }

    try {
        const response = await fetch(
            `${API_BASE}/sessions/${currentSession.id}/canvas-config?course_id=${courseId}&assignment_id=${assignmentId}&use_prod=${useProd}`,
            { method: 'PUT' }
        );

        if (!response.ok) {
            throw new Error('Failed to update Canvas configuration');
        }

        const result = await response.json();
        alert(`Canvas target updated!\n\nEnvironment: ${result.environment}\nCourse: ${result.course_name}\nAssignment: ${result.assignment_name}`);

        // Close dialog and reload session
        document.getElementById('canvas-target-dialog').style.display = 'none';

        // Refresh session data
        const sessionResponse = await fetch(`${API_BASE}/sessions/${currentSession.id}`);
        currentSession = await sessionResponse.json();
        updateSessionInfo();

    } catch (error) {
        console.error('Failed to update Canvas config:', error);
        alert('Failed to update Canvas configuration. Please try again.');
    }
};

// Export session button
document.getElementById('export-session-btn').onclick = async () => {
    if (!currentSession) return;

    try {
        // Fetch export data
        const response = await fetch(`${API_BASE}/sessions/${currentSession.id}/export`);

        if (!response.ok) {
            throw new Error('Export failed');
        }

        // Get filename from Content-Disposition header or generate default
        const contentDisposition = response.headers.get('Content-Disposition');
        let filename = `grading_session_${currentSession.id}.json`;
        if (contentDisposition) {
            const filenameMatch = contentDisposition.match(/filename="?([^"]+)"?/);
            if (filenameMatch) {
                filename = filenameMatch[1];
            }
        }

        // Download the file
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);

        alert('Session exported successfully! Save this file to resume grading later.');

    } catch (error) {
        console.error('Export failed:', error);
        alert('Failed to export session. Please try again.');
    }
};

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

        connectToFinalizationStream();

    } catch (error) {
        console.error('Finalization failed:', error);
        alert('Failed to start finalization: ' + error.message);
    }
};

// Listen for finalization status via SSE
let finalizationEventSource = null;

function connectToFinalizationStream() {
    // Close existing connection if any
    if (finalizationEventSource) {
        finalizationEventSource.close();
    }

    const streamUrl = `${API_BASE}/finalize/${currentSession.id}/finalize-stream`;
    console.log('Connecting to finalization SSE stream:', streamUrl);

    finalizationEventSource = new EventSource(streamUrl);

    finalizationEventSource.addEventListener('connected', (e) => {
        console.log('SSE connected for finalization progress');
    });

    finalizationEventSource.addEventListener('start', (e) => {
        const data = JSON.parse(e.data);
        console.log('Finalization started:', data);
        document.getElementById('finalization-message').textContent = data.message;
    });

    finalizationEventSource.addEventListener('progress', (e) => {
        const data = JSON.parse(e.data);
        console.log('Finalization progress:', data);

        document.getElementById('finalization-message').textContent = data.message;
        document.getElementById('finalization-progress-bar').style.width = `${data.progress}%`;
    });

    finalizationEventSource.addEventListener('complete', (e) => {
        const data = JSON.parse(e.data);
        console.log('Finalization complete:', data);

        finalizationEventSource.close();
        finalizationEventSource = null;

        document.getElementById('finalization-progress-bar').style.width = '100%';
        showNotification('Finalization complete! All grades have been uploaded to Canvas. 🎉', () => {
            location.reload();
        });
    });

    finalizationEventSource.addEventListener('error', (e) => {
        console.error('Finalization SSE error:', e);

        if (finalizationEventSource && finalizationEventSource.readyState === EventSource.CLOSED) {
            console.log('SSE connection closed');
            finalizationEventSource = null;
        } else {
            document.getElementById('finalization-progress').style.backgroundColor = '#fee2e2';
            document.getElementById('finalization-message').textContent = 'Connection error during finalization';
        }
    });
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
