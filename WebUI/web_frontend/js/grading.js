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
    setupProblemImageResize();
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
    const cachedMax = problemMaxPoints[currentProblemNumber];

    // Default to 8 if not set
    const maxPoints = cachedMax || 8;

    maxPointsInput.value = maxPoints;
    scoreInput.max = maxPoints;
}

// Load available problem numbers
async function loadProblemNumbers() {
    try {
        const [numbersResponse, statsResponse] = await Promise.all([
            fetch(`${API_BASE}/sessions/${currentSession.id}/problem-numbers`),
            fetch(`${API_BASE}/sessions/${currentSession.id}/stats`)
        ]);

        const data = await numbersResponse.json();
        const stats = await statsResponse.json();
        availableProblemNumbers = data.problem_numbers;

        // Build a map of problem number -> ungraded count
        const ungradedCounts = {};
        stats.problem_stats.forEach(ps => {
            ungradedCounts[ps.problem_number] = ps.num_total - ps.num_graded;
        });

        const select = document.getElementById('problem-select');
        select.innerHTML = '';

        availableProblemNumbers.forEach(num => {
            const option = document.createElement('option');
            option.value = num;
            const ungradedCount = ungradedCounts[num] || 0;
            option.textContent = `Problem ${num} (${ungradedCount})`;
            select.appendChild(option);
        });

        currentProblemNumber = availableProblemNumbers[0] || 1;
        select.value = currentProblemNumber;
        select.onchange = async () => {
            currentProblemNumber = parseInt(select.value);
            updateMaxPointsDropdown();
            await updateOverallProgress(); // Update progress bar when changing problems
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

        // Update current problem progress background (red bar showing remaining work)
        const currentProblemStats = stats.problem_stats.find(p => p.problem_number === currentProblemNumber);
        if (currentProblemStats && stats.total_problems > 0) {
            // Calculate how much of this problem's work is graded and remaining
            const problemGraded = currentProblemStats.num_graded / stats.total_problems * 100;
            const problemRemaining = (currentProblemStats.num_total - currentProblemStats.num_graded) / stats.total_problems * 100;

            // Calculate where the current problem starts in the overall progress
            // This is the sum of all completed work before the current problem
            let workBeforeCurrent = 0;
            for (const ps of stats.problem_stats) {
                if (ps.problem_number < currentProblemNumber) {
                    workBeforeCurrent += ps.num_graded;
                } else if (ps.problem_number === currentProblemNumber) {
                    break;
                }
            }
            const currentProblemStartPercent = workBeforeCurrent / stats.total_problems * 100;

            // Dark gray outline: shows the entire current problem (both graded and remaining)
            const grayOutline = document.getElementById('current-problem-graded-outline');
            grayOutline.style.left = `${currentProblemStartPercent}%`;
            grayOutline.style.width = `${problemGraded + problemRemaining}%`;

            // Red bar: shows remaining work in current problem
            const redBarStart = currentProblemStartPercent + problemGraded;
            const redBar = document.getElementById('current-problem-progress-bg');
            redBar.style.left = `${redBarStart}%`;
            redBar.style.width = `${problemRemaining}%`;

            // Yellow bar: shows blank-detected problems in UNGRADED portion of current problem
            // Sized and positioned relative to the current problem's total count
            const numBlankUngraded = currentProblemStats.num_blank_ungraded || 0;
            const numTotalInProblem = currentProblemStats.num_total;

            // Calculate width as proportion of total problems (to match gray box scale)
            const blankWidth = (numBlankUngraded / stats.total_problems) * 100;

            // Position at right end of the gray box (gray box ends at currentProblemStartPercent + problemGraded + problemRemaining)
            const grayBoxEnd = currentProblemStartPercent + problemGraded + problemRemaining;
            const yellowBarStart = grayBoxEnd - blankWidth;

            const yellowBar = document.getElementById('blank-problems-bar');
            yellowBar.style.left = `${yellowBarStart}%`;
            yellowBar.style.width = `${blankWidth}%`;

            console.log(`[DEBUG] Blank bar in current problem ${currentProblemNumber}: ${numBlankUngraded} blank (ungraded) out of ${numTotalInProblem} total, width=${blankWidth.toFixed(1)}%, left=${yellowBarStart.toFixed(1)}%`);
        } else {
            // No current problem or no data - hide all bars
            document.getElementById('current-problem-progress-bg').style.width = '0%';
            document.getElementById('current-problem-graded-outline').style.width = '0%';
            document.getElementById('blank-problems-bar').style.width = '0%';
        }
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

// Setup score sync between slider and input (slider removed, keeping function for compatibility)
function setupScoreSync() {
    // Slider has been removed - this function is now a no-op
    // Kept for compatibility with existing calls
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
            // Update input max
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

    // Don't handle if notification overlay is visible
    const notificationOverlay = document.getElementById('notification-overlay');
    if (notificationOverlay && notificationOverlay.style.display === 'flex') {
        return;
    }

    // Don't handle if default feedback dialog is open
    const defaultFeedbackDialog = document.getElementById('edit-default-feedback-dialog');
    if (defaultFeedbackDialog && defaultFeedbackDialog.style.display === 'flex') {
        return;
    }

    // Don't handle if add tag dialog is open
    const addTagDialog = document.getElementById('add-tag-dialog');
    if (addTagDialog && addTagDialog.style.display === 'flex') {
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

    // Number keys 0-9 or dash (-) - quick score entry (but not when typing in rubric table or other inputs)
    // Also ignore if any modifier keys are pressed (Cmd/Ctrl/Alt for browser shortcuts like zoom)
    if ((/^[0-9]$/.test(e.key) || e.key === '-') &&
        !e.metaKey && !e.ctrlKey && !e.altKey && // Ignore if modifier keys are held
        e.target.id !== 'score-input' &&
        e.target.id !== 'feedback-input' &&
        e.target.id !== 'max-points-input' &&
        !e.target.classList.contains('rubric-points') &&
        !e.target.classList.contains('rubric-description')) {
        e.preventDefault();
        document.getElementById('score-input').value = e.key;
        document.getElementById('score-input').focus();
    }
}

// Display the current problem (common display logic)
function displayCurrentProblem() {
    if (!currentProblem) return;

    // Display problem
    const problemImage = document.getElementById('problem-image');
    problemImage.src = `data:image/png;base64,${currentProblem.image_data}`;

    // Auto-size container to fit image when it loads
    problemImage.onload = () => {
        const scrollContainer = document.getElementById('problem-scroll-container');
        if (scrollContainer) {
            // Calculate the full displayed height of the image
            const displayedHeight = problemImage.offsetHeight;
            const fullImageHeight = displayedHeight + 40; // Add padding for borders/margins

            // Store this as the maximum allowed height (so user can always expand to see full image)
            scrollContainer.dataset.maxImageHeight = fullImageHeight;

            // Check if we have a saved height preference
            const savedHeight = localStorage.getItem('problemScrollContainerHeight');
            if (!savedHeight) {
                // No saved preference - default to showing full image
                scrollContainer.style.height = `${fullImageHeight}px`;
            }
            // If there's a saved height, the setupProblemImageResize() function already applied it
        }
    };

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

    // Show/hide "Show Answer" button based on QR data availability
    const showAnswerBtn = document.getElementById('show-answer-btn');
    if (currentProblem.has_qr_data) {
        showAnswerBtn.style.display = 'inline-block';
    } else {
        showAnswerBtn.style.display = 'none';
    }

    // Update answer dialog if it's currently visible
    const answerDialog = document.getElementById('answer-dialog');
    if (answerDialog && answerDialog.style.display === 'flex') {
        updateAnswerDialog();
    }

    // Update transcription dialog if it's currently visible
    const transcriptionDialog = document.getElementById('transcription-dialog');
    if (transcriptionDialog && transcriptionDialog.style.display === 'flex') {
        updateTranscriptionDialog();
    }

    // Populate form based on whether it's graded or blank
    if (currentProblem.graded) {
        // Already graded - show existing grade
        document.getElementById('score-input').value = currentProblem.score != null ? currentProblem.score : '';
        document.getElementById('feedback-input').value = currentProblem.feedback || '';

        // Remove blank indicator
        const oldBlankIndicator = document.getElementById('blank-indicator');
        if (oldBlankIndicator) oldBlankIndicator.remove();

        // Remove AI indicator
        const oldAiIndicator = document.getElementById('ai-graded-indicator');
        if (oldAiIndicator) oldAiIndicator.remove();
    } else if (currentProblem.is_blank) {
        // Don't auto-populate score for heuristically detected blanks - let user verify
        document.getElementById('score-input').value = '';
        document.getElementById('feedback-input').value = '';

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
        const oldBlankIndicator = document.getElementById('blank-indicator');
        if (oldBlankIndicator) oldBlankIndicator.remove();

        // Check if this is an AI-graded problem (has score and feedback but not yet graded)
        if (currentProblem.score != null && currentProblem.feedback) {
            // Auto-populate both score and feedback for review
            document.getElementById('score-input').value = currentProblem.score != null ? currentProblem.score : '';
            document.getElementById('feedback-input').value = currentProblem.feedback || '';

            // Show AI-graded indicator
            const aiIndicator = document.createElement('div');
            aiIndicator.id = 'ai-graded-indicator';
            aiIndicator.style.cssText = `
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 15px;
                border-radius: 8px;
                margin-bottom: 15px;
                box-shadow: 0 2px 8px rgba(102, 126, 234, 0.3);
            `;
            aiIndicator.innerHTML = `
                <strong>🤖 AI-Graded (Needs Review)</strong>
                <div style="font-size: 12px; margin-top: 5px; opacity: 0.9;">
                    Review and modify the score and feedback as needed, then submit
                </div>
            `;

            // Remove old indicator if exists
            const oldAiIndicator = document.getElementById('ai-graded-indicator');
            if (oldAiIndicator) oldAiIndicator.remove();

            // Insert before the problem image
            const problemContainer = document.querySelector('.problem-container');
            problemContainer.parentNode.insertBefore(aiIndicator, problemContainer);
        } else {
            // Clear form for non-AI-graded problems
            document.getElementById('score-input').value = '';
            document.getElementById('feedback-input').value = '';

            // Remove AI indicator if it exists
            const oldAiIndicator = document.getElementById('ai-graded-indicator');
            if (oldAiIndicator) oldAiIndicator.remove();
        }
    }

    // Load feedback tags and default feedback for this problem number
    if (currentSession && currentProblemNumber) {
        loadFeedbackTags(currentSession.id, currentProblemNumber);
        loadDefaultFeedback(currentSession.id, currentProblemNumber);
    }

    // Load explanation from QR code if available
    loadExplanation();
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

    // Auto-apply selected tags before submitting
    if (typeof applySelectedTags === 'function' && selectedTagIds && selectedTagIds.size > 0) {
        await applySelectedTags();
    }

    const scoreValue = document.getElementById('score-input').value.trim();
    const maxPoints = problemMaxPoints[currentProblemNumber] || 8;

    // Check if it's a dash (for blank marking) or a number
    let score;
    let isBlank = false;
    if (scoreValue === '-') {
        score = '-';  // Send dash as-is to backend
        isBlank = true;
    } else {
        score = parseFloat(scoreValue);
        if (isNaN(score)) {
            alert('Please enter a valid score or "-" to mark as blank');
            return;
        }
        if (score > maxPoints) {
            alert(`Score cannot exceed ${maxPoints} points`);
            return;
        }
    }

    // Auto-apply default feedback if conditions are met
    if (typeof shouldApplyDefaultFeedback === 'function' && shouldApplyDefaultFeedback(score === '-' ? 0 : score, isBlank)) {
        applyDefaultFeedbackToTextarea();
    }

    let feedback = document.getElementById('feedback-input').value;

    // Auto-include explanation if checkbox is enabled and explanation is available
    const includeExplanation = document.getElementById('include-explanation-checkbox');
    if (includeExplanation && includeExplanation.checked && currentProblem && explanationCache[currentProblem.id]) {
        const explanation = explanationCache[currentProblem.id];
        const explanationWithDisclaimer = 'Note: The explanation below is automatically generated and might not be correct.\n\n' + explanation;

        if (feedback.trim()) {
            // Append explanation with separator if there's existing feedback
            feedback = feedback + '\n\n---\n\n' + explanationWithDisclaimer;
        } else {
            // Use explanation alone if no custom feedback
            feedback = explanationWithDisclaimer;
        }
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

// Toggle student scores visibility
function toggleStudentScores() {
    const container = document.getElementById('student-scores-container');
    const toggle = document.getElementById('student-scores-toggle');

    if (container.style.display === 'none') {
        container.style.display = 'block';
        toggle.textContent = '▼';
    } else {
        container.style.display = 'none';
        toggle.textContent = '▶';
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

        // Calculate overall statistics based on what's been graded so far
        let examStatsHtml = '';
        const studentsWithGrades = scoresData.students.filter(s => s.total_score !== null && s.graded_problems > 0);

        if (studentsWithGrades.length > 0) {
            // Get raw scores
            const rawScores = studentsWithGrades.map(s => s.total_score);
            const rawMin = Math.min(...rawScores);
            const rawMax = Math.max(...rawScores);
            const rawAvg = rawScores.reduce((sum, s) => sum + s, 0) / rawScores.length;

            // Calculate raw standard deviation
            const rawVariance = rawScores.reduce((sum, s) => sum + Math.pow(s - rawAvg, 2), 0) / rawScores.length;
            const rawStddev = Math.sqrt(rawVariance);

            // Calculate normalized scores (percentage of points earned out of points graded)
            const normalizedScores = studentsWithGrades.map(s => {
                // Calculate max possible points for problems this student has been graded on
                // We need to figure out which problems they've been graded on
                // For now, approximate using their graded_problems count
                const problemsGraded = s.graded_problems;

                // Get the actual max points for problems based on graded count
                // Assume problems are graded in order (1, 2, 3, etc.)
                let maxPossibleForStudent = 0;
                const gradedProblemStats = stats.problem_stats.slice(0, problemsGraded);
                gradedProblemStats.forEach(ps => {
                    maxPossibleForStudent += (ps.max_points || 8);
                });

                // Return normalized score as percentage
                return maxPossibleForStudent > 0 ? (s.total_score / maxPossibleForStudent) * 100 : 0;
            });

            const normAvg = normalizedScores.reduce((sum, s) => sum + s, 0) / normalizedScores.length;

            // Calculate normalized standard deviation
            const normVariance = normalizedScores.reduce((sum, s) => sum + Math.pow(s - normAvg, 2), 0) / normalizedScores.length;
            const normStddev = Math.sqrt(normVariance);

            // Calculate total possible points across all problems
            const totalPossible = stats.problem_stats.reduce((sum, ps) => {
                return sum + (ps.max_points || 8);
            }, 0);

            // Calculate Canvas grade (raw score out of 100)
            const canvasAvg = rawAvg; // Canvas grade is the raw score (out of 100)
            const canvasPercentage = canvasAvg; // Since it's out of 100, the score IS the percentage

            examStatsHtml = `
                <h3>Overall Progress Statistics <small style="font-size: 14px; font-weight: normal; color: var(--gray-600);">(${studentsWithGrades.length} students with grades, based on problems graded so far)</small></h3>
                <div class="overall-stats" style="margin-bottom: 30px;">
                    <div class="stat-card">
                        <h3>Average Score</h3>
                        <div class="value">${rawAvg.toFixed(2)} pts</div>
                        <div style="font-size: 14px; color: var(--gray-600); margin-top: 5px;">(of graded problems)</div>
                    </div>
                    <div class="stat-card">
                        <h3>Canvas Grade</h3>
                        <div class="value">${canvasPercentage.toFixed(1)}%</div>
                        <div style="font-size: 14px; color: var(--gray-600); margin-top: 5px;">(out of 100)</div>
                    </div>
                    <div class="stat-card">
                        <h3>Normalized Average</h3>
                        <div class="value">${normAvg.toFixed(1)}%</div>
                        <div style="font-size: 14px; color: var(--gray-600); margin-top: 5px;">±${normStddev.toFixed(1)}%</div>
                    </div>
                    <div class="stat-card">
                        <h3>Score Range</h3>
                        <div class="value">${rawMin.toFixed(2)} - ${rawMax.toFixed(2)}</div>
                        <div style="font-size: 14px; color: var(--gray-600); margin-top: 5px;">Min to Max</div>
                    </div>
                    <div class="stat-card">
                        <h3>Std Deviation</h3>
                        <div class="value">±${rawStddev.toFixed(2)} pts</div>
                        <div style="font-size: 14px; color: var(--gray-600); margin-top: 5px;">(raw scores)</div>
                    </div>
                </div>
            `;
        }

        container.innerHTML = examStatsHtml + `
            <h3>Grading Progress</h3>
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
            container.innerHTML += '<h3 style="margin-top: 30px;">Per-Problem Statistics <small style="font-size: 14px; font-weight: normal; color: var(--gray-600);">(click a card to review)</small></h3>';
            const problemStatsHtml = stats.problem_stats.map(ps => {
                const problemProgress = ps.num_total > 0 ? (ps.num_graded / ps.num_total * 100) : 0;

                // Format statistics with fallbacks
                const avgText = ps.avg_score !== null && ps.avg_score !== undefined ? ps.avg_score.toFixed(2) : 'N/A';
                const minText = ps.min_score !== null && ps.min_score !== undefined ? ps.min_score.toFixed(2) : 'N/A';
                const maxText = ps.max_score !== null && ps.max_score !== undefined ? ps.max_score.toFixed(2) : 'N/A';
                const medianText = ps.median_score !== null && ps.median_score !== undefined ? ps.median_score.toFixed(2) : 'N/A';
                const stddevText = ps.stddev_score !== null && ps.stddev_score !== undefined ? ps.stddev_score.toFixed(2) : 'N/A';

                // Format mean ± stddev
                const meanPlusMinusText = (ps.avg_score !== null && ps.avg_score !== undefined && ps.stddev_score !== null && ps.stddev_score !== undefined)
                    ? `${ps.avg_score.toFixed(2)} ± ${ps.stddev_score.toFixed(2)}`
                    : 'N/A';

                // Format normalized mean ± normalized stddev (as percentages)
                const meanNormPlusMinusText = (ps.mean_normalized !== null && ps.mean_normalized !== undefined && ps.stddev_normalized !== null && ps.stddev_normalized !== undefined)
                    ? `${(ps.mean_normalized * 100).toFixed(1)}% ± ${(ps.stddev_normalized * 100).toFixed(1)}%`
                    : 'N/A';

                const maxPointsText = ps.max_points !== null && ps.max_points !== undefined ? ps.max_points.toFixed(1) : 'N/A';

                // Format blank % with highlight if high skip rate
                let pctBlankDisplay;
                const hasHighSkipRate = ps.pct_blank !== null && ps.pct_blank !== undefined && ps.pct_blank > 25;
                if (ps.pct_blank !== null && ps.pct_blank !== undefined) {
                    const blankValue = ps.pct_blank.toFixed(1) + '%';
                    if (hasHighSkipRate) {
                        pctBlankDisplay = `<div style="color: var(--gray-600); font-size: 12px; margin-bottom: 2px;">Blank %</div><div class="blank-pct-highlight">${blankValue}</div>`;
                    } else {
                        pctBlankDisplay = `<div style="color: var(--gray-600); font-size: 12px; margin-bottom: 2px;">Blank %</div><div style="font-weight: 600; font-size: 16px;">${blankValue}</div>`;
                    }
                } else {
                    pctBlankDisplay = '<div style="color: var(--gray-600); font-size: 12px; margin-bottom: 2px;">Blank %</div><div style="font-weight: 600; font-size: 16px;">N/A</div>';
                }

                // Determine CSS classes for visual indicators
                let cssClasses = 'stat-card';

                // Completion indicator - add 'fully-graded' class if all problems graded
                if (ps.num_graded >= ps.num_total && ps.num_total > 0) {
                    cssClasses += ' fully-graded';
                }

                // Performance indicator - add class based on normalized mean
                // Only add if we have valid data
                if (ps.mean_normalized !== null && ps.mean_normalized !== undefined) {
                    if (ps.mean_normalized >= 0.9) {
                        cssClasses += ' performance-excellent';  // 90%+
                    } else if (ps.mean_normalized >= 0.75) {
                        cssClasses += ' performance-good';       // 75-89%
                    } else if (ps.mean_normalized >= 0.6) {
                        cssClasses += ' performance-moderate';   // 60-74%
                    } else if (ps.mean_normalized >= 0.5) {
                        cssClasses += ' performance-poor';       // 50-59%
                    } else {
                        cssClasses += ' performance-verypoor';   // <50%
                    }
                }

                return `
                    <div class="${cssClasses}" data-problem-number="${ps.problem_number}" style="cursor: pointer; transition: all 0.2s;"
                         onmouseenter="this.style.transform='translateY(-4px)'; this.style.boxShadow='0 4px 12px rgba(0,0,0,0.15)';"
                         onmouseleave="this.style.transform=''; this.style.boxShadow='';"
                         onclick="reviewProblemFromStats(${ps.problem_number})">
                        <h3 style="margin-bottom: 12px; border-bottom: 2px solid var(--primary-color); padding-bottom: 8px;">Problem ${ps.problem_number}</h3>

                        <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 8px; margin-bottom: 8px;">
                            <div style="text-align: left;">
                                <div style="color: var(--gray-600); font-size: 12px; margin-bottom: 2px;">Mean ± Std Dev</div>
                                <div style="font-weight: 600; font-size: 16px;">${meanPlusMinusText}</div>
                            </div>
                            <div style="text-align: right;">
                                <div style="color: var(--gray-600); font-size: 12px; margin-bottom: 2px;">Median</div>
                                <div style="font-weight: 600; font-size: 16px;">${medianText}</div>
                            </div>
                        </div>
                        <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 8px; margin-bottom: 12px;">
                            <div style="text-align: left;">
                                <div style="color: var(--gray-600); font-size: 12px; margin-bottom: 2px;">Normalized</div>
                                <div style="font-weight: 600; font-size: 16px;">${meanNormPlusMinusText}</div>
                            </div>
                            <div style="text-align: right;">
                                ${pctBlankDisplay}
                            </div>
                        </div>

                        <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 12px; padding-top: 8px; border-top: 1px solid var(--gray-200);">
                            <div style="text-align: center;">
                                <div style="color: var(--gray-600); font-size: 11px;">Min</div>
                                <div style="font-weight: 500;">${minText}</div>
                            </div>
                            <div style="text-align: center;">
                                <div style="color: var(--gray-600); font-size: 11px;">Max</div>
                                <div style="font-weight: 500;">${maxText}</div>
                            </div>
                            <div style="text-align: center;">
                                <div style="color: var(--gray-600); font-size: 11px;">Out of</div>
                                <div style="font-weight: 500;">${maxPointsText}</div>
                            </div>
                        </div>

                        <div style="padding-top: 8px; border-top: 1px solid var(--gray-200); text-align: center;">
                            <div style="color: var(--gray-700); font-size: 13px; font-weight: 500;">
                                Graded: ${ps.num_graded} / ${ps.num_total} (${problemProgress.toFixed(0)}%)
                            </div>
                        </div>
                    </div>
                `;
            }).join('');
            container.innerHTML += '<div class="problem-stats-grid">' + problemStatsHtml + '</div>';
        }

        // Add student scores table (collapsible, hidden by default)
        if (scoresData.students.length > 0) {
            container.innerHTML += `
                <h3 style="margin-top: 30px; cursor: pointer; user-select: none;"
                    id="student-scores-header"
                    onclick="toggleStudentScores()"
                    title="Click to expand/collapse">
                    <span id="student-scores-toggle">▶</span> Student Scores (${scoresData.students.length})
                </h3>
            `;
            const studentScoresHtml = `
                <div id="student-scores-container" style="display: none;">
                    <table class="student-scores-table">
                        <thead>
                            <tr>
                                <th class="sortable" onclick="sortStudentTable('name')" data-sort="name">
                                    Student Name <span class="sort-indicator"></span>
                                </th>
                                <th class="sortable" onclick="sortStudentTable('progress')" data-sort="progress">
                                    Progress <span class="sort-indicator"></span>
                                </th>
                                <th class="sortable" onclick="sortStudentTable('score')" data-sort="score">
                                    Total Score <span class="sort-indicator"></span>
                                </th>
                            </tr>
                        </thead>
                        <tbody id="student-scores-tbody">
                            ${scoresData.students.map(s => `
                                <tr class="${s.is_complete ? 'complete' : 'incomplete'}"
                                    data-name="${s.student_name || 'Unmatched'}"
                                    data-progress="${s.graded_problems / s.total_problems}"
                                    data-score="${s.total_score || 0}">
                                    <td>${s.student_name || 'Unmatched'}</td>
                                    <td>${s.graded_problems} / ${s.total_problems}</td>
                                    <td>${s.total_score ? s.total_score.toFixed(2) : '0.00'}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
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

// Re-scan QR code button (for current problem instance only)
document.getElementById('rescan-problem-qr-btn').onclick = async () => {
    if (!currentProblem) {
        alert('No problem loaded');
        return;
    }

    // Ask user for confirmation and DPI selection
    const dpiInput = prompt(
        `Re-scan QR code for this submission's Problem ${currentProblem.problem_number} at higher DPI?\n\n` +
        'Enter DPI value (default: 600, higher = better for complex QR codes):\n' +
        'Recommended values:\n' +
        '  - 600 (default): Good for 2x upsampling from 300 DPI scans\n' +
        '  - 450: Lighter, faster processing\n' +
        '  - 900: Very high quality, slower (3x upsampling)',
        '600'
    );

    if (!dpiInput) {
        // User cancelled
        return;
    }

    const dpi = parseInt(dpiInput);
    if (isNaN(dpi) || dpi < 72 || dpi > 1200) {
        alert('Invalid DPI value. Please enter a number between 72 and 1200.');
        return;
    }

    try {
        // Disable button during processing
        const rescanBtn = document.getElementById('rescan-problem-qr-btn');
        const originalText = rescanBtn.textContent;
        rescanBtn.disabled = true;
        rescanBtn.textContent = '🔄 Scanning...';

        // Make API call to rescan QR code for this specific problem instance
        const response = await fetch(`${API_BASE}/problems/${currentProblem.id}/rescan-qr?dpi=${dpi}`, {
            method: 'POST'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'QR re-scan failed');
        }

        const result = await response.json();

        // Show results
        if (result.qr_found) {
            alert(`QR Code Found!\n\n` +
                  `Problem ${result.problem_number}\n` +
                  `Max Points: ${result.max_points}\n` +
                  `DPI: ${result.dpi_used}`);

            // Reload max points and update UI
            await loadProblemMaxPoints();
            updateMaxPointsDropdown();

            // Update the current problem's has_qr_data flag
            currentProblem.has_qr_data = true;

            // Show the "Show Answer" button now that we have QR data
            const showAnswerBtn = document.getElementById('show-answer-btn');
            if (showAnswerBtn) {
                showAnswerBtn.style.display = 'inline-block';
            }
        } else {
            alert(`No QR Code Found\n\n` +
                  `Problem ${result.problem_number}\n` +
                  `DPI Used: ${result.dpi_used}\n\n` +
                  `Try increasing the DPI or check if the QR code is present on the exam.`);
        }

    } catch (error) {
        console.error('QR re-scan failed:', error);
        alert(`Failed to re-scan QR code: ${error.message}`);
    } finally {
        // Re-enable button
        const rescanBtn = document.getElementById('rescan-problem-qr-btn');
        rescanBtn.disabled = false;
        rescanBtn.textContent = originalText;
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

        // Show progress area IMMEDIATELY to provide feedback
        const progressDiv = document.getElementById('finalization-progress');
        const messageDiv = document.getElementById('finalization-message');
        const progressBar = document.getElementById('finalization-progress-bar');

        progressDiv.style.display = 'block';
        messageDiv.textContent = 'Initializing finalization...';
        progressBar.style.width = '0%';
        document.getElementById('finalize-btn').disabled = true;

        // Start finalization
        const response = await fetch(`${API_BASE}/finalize/${currentSession.id}/finalize`, {
            method: 'POST'
        });

        if (!response.ok) {
            const error = await response.json();
            // Reset UI on error
            progressDiv.style.display = 'none';
            document.getElementById('finalize-btn').disabled = false;
            throw new Error(error.detail || 'Finalization failed');
        }

        // Update message once server responds
        messageDiv.textContent = 'Starting finalization...';

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
async function fetchTranscription(problemId, model = 'default') {
    // Normalize 'default' to 'ollama' for caching since they use the same backend model
    const cacheKey = model === 'default' ? 'ollama' : model;

    // Check cache first
    if (transcriptionCache[problemId] && transcriptionCache[problemId][cacheKey]) {
        console.log(`Using cached ${cacheKey} transcription for problem ${problemId}`);
        return transcriptionCache[problemId][cacheKey];
    }

    console.log(`Fetching new ${model} transcription for problem ${problemId}`);

    // Fetch from API
    const url = `${API_BASE}/problems/${problemId}/decipher?model=${model}`;
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

    console.log(`Cached ${cacheKey} transcription for problem ${problemId}`);

    return transcriptionCache[problemId][cacheKey];
}

// Function to display transcription in dialog
function displayTranscription(transcription) {
    transcriptionText.textContent = transcription.text;
    modelUsed.textContent = `Model used: ${transcription.model}`;

    // Show model selection buttons
    transcriptionActions.style.display = 'block';
    transcriptionActions.innerHTML = `
        <div style="display: flex; gap: 10px; flex-wrap: wrap; margin-top: 10px;">
            <button id="retry-ollama-btn" class="btn-secondary" style="flex: 1; min-width: 120px;">
                Try Ollama
            </button>
            <button id="retry-sonnet-btn" class="btn-secondary" style="flex: 1; min-width: 120px;">
                Try Sonnet
            </button>
            <button id="retry-opus-btn" class="btn-secondary" style="flex: 1; min-width: 120px;">
                Try Opus (Premium)
            </button>
        </div>
    `;

    // Add event listeners for the new buttons
    document.getElementById('retry-ollama-btn').addEventListener('click', () => retryWithModel('ollama'));
    document.getElementById('retry-sonnet-btn').addEventListener('click', () => retryWithModel('sonnet'));
    document.getElementById('retry-opus-btn').addEventListener('click', () => retryWithModel('opus'));
}

// Function to retry transcription with a specific model
async function retryWithModel(model) {
    if (!currentProblem) return;

    const modelNames = {
        'ollama': 'Ollama (Local)',
        'sonnet': 'Sonnet',
        'opus': 'Opus (Premium)'
    };

    // Show loading state
    transcriptionText.innerHTML = `<div class="transcription-loading">Transcribing with ${modelNames[model]}...</div>`;
    transcriptionActions.style.display = 'none';

    try {
        const transcription = await fetchTranscription(currentProblem.id, model);
        displayTranscription(transcription);
    } catch (error) {
        console.error(`Failed to decipher with ${model}:`, error);
        transcriptionText.innerHTML = `<div style="color: var(--danger-color);">Failed to transcribe with ${modelNames[model]}. Please try again.</div>`;
        // Show buttons again so user can retry
        transcriptionActions.style.display = 'block';
    }
}

// Function to update transcription dialog when problem changes
async function updateTranscriptionDialog() {
    if (!currentProblem) {
        transcriptionDialog.style.display = 'none';
        return;
    }

    // Check if we have a cached transcription for this problem (default to ollama)
    const cacheKey = 'ollama';
    if (transcriptionCache[currentProblem.id] && transcriptionCache[currentProblem.id][cacheKey]) {
        // Show cached transcription immediately
        console.log(`Showing cached transcription for problem ${currentProblem.id}`);
        displayTranscription(transcriptionCache[currentProblem.id][cacheKey]);
    } else {
        // No cache - fetch new transcription with Ollama
        console.log(`No cache found, fetching new transcription for problem ${currentProblem.id}`);
        transcriptionText.innerHTML = '<div class="transcription-loading">Transcribing handwriting with Ollama...</div>';
        transcriptionActions.style.display = 'none';

        try {
            const transcription = await fetchTranscription(currentProblem.id, 'default');
            displayTranscription(transcription);
        } catch (error) {
            console.error('Failed to auto-fetch transcription:', error);
            transcriptionText.innerHTML = '<div style="color: var(--danger-color);">Failed to transcribe handwriting. Please try again.</div>';
            transcriptionActions.style.display = 'block';
        }
    }
}

// Show in Context button
const showContextBtn = document.getElementById('show-context-btn');
const contextDialog = document.getElementById('context-dialog');
const closeContext = document.getElementById('close-context');
const contextPageImage = document.getElementById('context-page-image');
const contextHighlight = document.getElementById('context-highlight');

showContextBtn.addEventListener('click', async () => {
    if (!currentProblem) {
        alert('No problem loaded');
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/problems/${currentProblem.id}/context`);

        if (!response.ok) {
            if (response.status === 400) {
                alert('Context view not available for this problem (uses legacy storage)');
            } else {
                throw new Error(`Failed to fetch context: ${response.statusText}`);
            }
            return;
        }

        const data = await response.json();

        // Display the full page image
        contextPageImage.src = `data:image/png;base64,${data.page_image}`;

        // Wait for image to load before positioning highlight
        contextPageImage.onload = () => {
            const imgNaturalHeight = contextPageImage.naturalHeight;
            const imgNaturalWidth = contextPageImage.naturalWidth;
            const displayHeight = contextPageImage.offsetHeight;
            const displayWidth = contextPageImage.offsetWidth;

            // Scale region coordinates to displayed image size
            const scaleY = displayHeight / imgNaturalHeight;

            const highlightTop = data.problem_region.y_start * scaleY;
            const highlightHeight = (data.problem_region.y_end - data.problem_region.y_start) * scaleY;

            // Position the highlight box
            contextHighlight.style.top = `${highlightTop}px`;
            contextHighlight.style.left = '0px';
            contextHighlight.style.width = `${displayWidth}px`;
            contextHighlight.style.height = `${highlightHeight}px`;
        };

        // Show the dialog
        contextDialog.style.display = 'flex';

    } catch (error) {
        console.error('Failed to show context:', error);
        alert('Failed to load context view. Please try again.');
    }
});

closeContext.addEventListener('click', () => {
    contextDialog.style.display = 'none';
});

// Close on background click
contextDialog.addEventListener('click', (e) => {
    if (e.target === contextDialog) {
        contextDialog.style.display = 'none';
    }
});

// Decipher handwriting button (defaults to Ollama)
decipherBtn.addEventListener('click', async () => {
    if (!currentProblem) {
        alert('No problem loaded');
        return;
    }

    // Show dialog with loading state
    transcriptionText.innerHTML = '<div class="transcription-loading">Transcribing handwriting with Ollama...</div>';
    transcriptionActions.style.display = 'none';
    transcriptionDialog.style.display = 'flex';

    try {
        // Default to 'default' which uses Ollama
        const transcription = await fetchTranscription(currentProblem.id, 'default');
        displayTranscription(transcription);
    } catch (error) {
        console.error('Failed to decipher handwriting:', error);
        transcriptionText.innerHTML = '<div style="color: var(--danger-color);">Failed to transcribe handwriting. Please try again.</div>';

        // Show model selection buttons
        transcriptionActions.style.display = 'block';
        transcriptionActions.innerHTML = `
            <div style="display: flex; gap: 10px; flex-wrap: wrap; margin-top: 10px;">
                <button id="retry-sonnet-btn" class="btn btn-secondary" style="flex: 1; min-width: 120px;">
                    Try Sonnet
                </button>
                <button id="retry-opus-btn" class="btn btn-primary" style="flex: 1; min-width: 120px;">
                    Try Opus (Premium)
                </button>
            </div>
        `;

        // Add event listeners for the buttons
        document.getElementById('retry-sonnet-btn').addEventListener('click', () => retryWithModel('sonnet'));
        document.getElementById('retry-opus-btn').addEventListener('click', () => retryWithModel('opus'));
    }
});

// =============================================================================
// SHOW ANSWER FUNCTIONALITY
// =============================================================================

const showAnswerBtn = document.getElementById('show-answer-btn');
const answerDialog = document.getElementById('answer-dialog');
const closeAnswerX = document.getElementById('close-answer-x');

// Make answer dialog draggable
let isAnswerDragging = false;
let answerDragOffsetX = 0;
let answerDragOffsetY = 0;

document.querySelector('.answer-header').addEventListener('mousedown', (e) => {
    if (e.target.classList.contains('answer-close')) return;
    isAnswerDragging = true;
    const rect = answerDialog.getBoundingClientRect();
    answerDragOffsetX = e.clientX - rect.left;
    answerDragOffsetY = e.clientY - rect.top;
    answerDialog.style.transform = 'none';
});

document.addEventListener('mousemove', (e) => {
    if (!isAnswerDragging) return;
    answerDialog.style.left = (e.clientX - answerDragOffsetX) + 'px';
    answerDialog.style.top = (e.clientY - answerDragOffsetY) + 'px';
});

document.addEventListener('mouseup', () => {
    isAnswerDragging = false;
});

// Function to update answer dialog with current problem
async function updateAnswerDialog() {
    if (!currentProblem) {
        answerDialog.style.display = 'none';
        return;
    }

    // Check if current problem has QR data
    if (!currentProblem.has_qr_data) {
        // No QR data - show message
        const answerContent = document.getElementById('answer-content');
        const answerError = document.getElementById('answer-error');
        answerContent.style.display = 'none';
        answerError.style.display = 'block';
        answerError.textContent = 'Answer not available for this problem (no QR code data)';
        return;
    }

    // Has QR data - load the answer
    const answerContent = document.getElementById('answer-content');
    const answerList = document.getElementById('answer-list');
    const answerError = document.getElementById('answer-error');
    const answerMetadata = document.getElementById('answer-metadata');

    answerContent.style.display = 'block';
    answerError.style.display = 'none';
    answerList.innerHTML = '<div style="text-align: center; padding: 20px;">Loading answer...</div>';
    answerMetadata.style.display = 'none';

    try {
        const response = await fetch(`${API_BASE}/problems/${currentProblem.id}/regenerate-answer`);

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to load answer');
        }

        const data = await response.json();

        // Display metadata
        document.getElementById('answer-question-type').textContent = data.question_type;
        document.getElementById('answer-seed').textContent = data.seed;
        document.getElementById('answer-version').textContent = data.version;
        document.getElementById('answer-max-points').textContent = data.max_points;

        // Display config if available
        const configWrapper = document.getElementById('answer-config-wrapper');
        const configSpan = document.getElementById('answer-config');
        if (data.config) {
            configSpan.textContent = JSON.stringify(data.config);
        } else {
            configSpan.textContent = 'None';
        }
        configWrapper.style.display = 'block';

        answerMetadata.style.display = 'block';

        // Display HTML answer key if available, otherwise show individual answers
        if (data.answer_key_html) {
            answerList.innerHTML = `<div style="padding: 15px; background: white; border-radius: 4px;">${data.answer_key_html}</div>`;
        } else if (data.answers && data.answers.length > 0) {
            answerList.innerHTML = data.answers.map(answer => {
                let html = `<div style="margin-bottom: 15px; padding: 10px; background: white; border-radius: 4px;">`;
                html += `<div style="font-weight: 600; color: #1e40af; margin-bottom: 5px;">${answer.key}:</div>`;

                if (answer.html) {
                    html += `<div style="font-size: 18px; font-family: 'Courier New', monospace;">${answer.html}</div>`;
                } else {
                    html += `<div style="font-size: 18px; font-family: 'Courier New', monospace;">${answer.value}</div>`;
                }

                if (answer.tolerance !== undefined && answer.tolerance !== null) {
                    html += `<div style="font-size: 12px; color: #6b7280; margin-top: 5px;">Tolerance: ±${answer.tolerance}</div>`;
                }
                html += `</div>`;
                return html;
            }).join('');
        } else {
            answerList.innerHTML = '<div style="color: #6b7280;">No answers available</div>';
        }

        // Trigger MathJax typesetting for the answer content
        if (typeof MathJax !== 'undefined') {
            MathJax.typesetPromise([answerList]).catch((err) => console.error('MathJax typesetting failed:', err));
        }

    } catch (error) {
        console.error('Failed to load answer:', error);
        answerContent.style.display = 'none';
        answerError.style.display = 'block';
        answerError.textContent = error.message;
    }
}

// Show answer button
showAnswerBtn.addEventListener('click', async () => {
    if (!currentProblem) {
        alert('No problem loaded');
        return;
    }

    // Show dialog with loading state
    const answerContent = document.getElementById('answer-content');
    const answerList = document.getElementById('answer-list');
    const answerError = document.getElementById('answer-error');
    const answerMetadata = document.getElementById('answer-metadata');

    answerDialog.style.display = 'flex';
    answerContent.style.display = 'block';
    answerError.style.display = 'none';
    answerList.innerHTML = '<div style="text-align: center; padding: 20px;">Loading answer...</div>';
    answerMetadata.style.display = 'none';

    try {
        const response = await fetch(`${API_BASE}/problems/${currentProblem.id}/regenerate-answer`);

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to load answer');
        }

        const data = await response.json();

        // Display metadata
        document.getElementById('answer-question-type').textContent = data.question_type;
        document.getElementById('answer-seed').textContent = data.seed;
        document.getElementById('answer-version').textContent = data.version;
        document.getElementById('answer-max-points').textContent = data.max_points;

        // Display config if available (always show, with "None" if not present)
        const configWrapper = document.getElementById('answer-config-wrapper');
        const configSpan = document.getElementById('answer-config');
        if (data.config) {
            configSpan.textContent = JSON.stringify(data.config);
        } else {
            configSpan.textContent = 'None';
        }
        configWrapper.style.display = 'block';

        answerMetadata.style.display = 'block';

        // Display HTML answer key if available, otherwise show individual answers
        if (data.answer_key_html) {
            // Display the full HTML answer key
            answerList.innerHTML = `<div style="padding: 15px; background: white; border-radius: 4px;">${data.answer_key_html}</div>`;
        } else if (data.answers && data.answers.length > 0) {
            // Fallback to individual answers
            answerList.innerHTML = data.answers.map(answer => {
                let html = `<div style="margin-bottom: 15px; padding: 10px; background: white; border-radius: 4px;">`;
                html += `<div style="font-weight: 600; color: #1e40af; margin-bottom: 5px;">${answer.key}:</div>`;

                // Use HTML rendering if available, otherwise fall back to plain text
                if (answer.html) {
                    html += `<div style="font-size: 18px; font-family: 'Courier New', monospace;">${answer.html}</div>`;
                } else {
                    html += `<div style="font-size: 18px; font-family: 'Courier New', monospace;">${answer.value}</div>`;
                }

                if (answer.tolerance !== undefined && answer.tolerance !== null) {
                    html += `<div style="font-size: 12px; color: #6b7280; margin-top: 5px;">Tolerance: ±${answer.tolerance}</div>`;
                }
                html += `</div>`;
                return html;
            }).join('');
        } else {
            answerList.innerHTML = '<div style="color: #6b7280;">No answers available</div>';
        }

        // Trigger MathJax typesetting for the answer content
        if (typeof MathJax !== 'undefined') {
            MathJax.typesetPromise([answerList]).catch((err) => console.error('MathJax typesetting failed:', err));
        }

    } catch (error) {
        console.error('Failed to load answer:', error);
        answerContent.style.display = 'none';
        answerError.style.display = 'block';
        answerError.textContent = error.message;
    }
});

// Close answer dialog
closeAnswerX.addEventListener('click', () => {
    answerDialog.style.display = 'none';
});

// Close on background click
answerDialog.addEventListener('click', (e) => {
    if (e.target === answerDialog) {
        answerDialog.style.display = 'none';
    }
});


// =============================================================================
// AUTOGRADING FUNCTIONALITY
// =============================================================================

let autogradingEventSource = null;

// Start Autograding button
const startAutogradeBtn = document.getElementById('start-autograde-btn');
startAutogradeBtn.addEventListener('click', async () => {
    if (!currentSession || !currentProblemNumber) return;

    // Show modal with extract phase
    const modal = document.getElementById('autograding-modal');
    const extractPhase = document.getElementById('autograding-extract-phase');
    const verifyPhase = document.getElementById('autograding-verify-phase');
    const progressPhase = document.getElementById('autograding-progress-phase');

    modal.style.display = 'flex';
    extractPhase.style.display = 'block';
    verifyPhase.style.display = 'none';
    progressPhase.style.display = 'none';

    try {
        // Extract question text
        const response = await fetch(`${API_BASE}/ai-grader/${currentSession.id}/extract-question`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ problem_number: currentProblemNumber })
        });

        if (!response.ok) {
            throw new Error('Failed to extract question text');
        }

        const data = await response.json();

        // Show verify phase
        extractPhase.style.display = 'none';
        verifyPhase.style.display = 'block';

        const questionTextArea = document.getElementById('autograding-question-text');
        questionTextArea.value = data.question_text;

        // Try to load existing rubric if available
        try {
            const rubricResponse = await fetch(`${API_BASE}/ai-grader/${currentSession.id}/rubric/${currentProblemNumber}`);
            if (rubricResponse.ok) {
                const rubricData = await rubricResponse.json();
                if (rubricData.rubric) {
                    // Render as table (rubric-table.js provides this function)
                    renderRubricTable(rubricData.rubric);
                }
            }
        } catch (error) {
            console.log('No existing rubric found, starting fresh');
        }

    } catch (error) {
        console.error('Failed to extract question:', error);
        modal.style.display = 'none';
        showNotification(`Failed to extract question: ${error.message}`);
    }
});

// Cancel autograding
document.getElementById('autograding-cancel-btn').onclick = () => {
    const modal = document.getElementById('autograding-modal');
    modal.style.display = 'none';
};

// Generate rubric button
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
    const rubricTextarea = document.getElementById('autograding-rubric-text');
    const rubricLoading = document.getElementById('rubric-loading');
    const generateBtn = document.getElementById('generate-rubric-btn');

    rubricLoading.style.display = 'block';
    rubricTextarea.style.display = 'none';
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
        rubricTextarea.value = data.rubric;

    } catch (error) {
        console.error('Failed to generate rubric:', error);
        alert(`Failed to generate rubric: ${error.message}\n\nMake sure you have manually graded at least 3 submissions for this problem first.`);
    } finally {
        rubricLoading.style.display = 'none';
        rubricTextarea.style.display = 'block';
        generateBtn.disabled = false;
    }
};

// Confirm and start autograding
document.getElementById('autograding-confirm-btn').onclick = async () => {
    const questionText = document.getElementById('autograding-question-text').value;
    const rubricText = document.getElementById('autograding-rubric-text').value;

    if (!questionText.trim()) {
        alert('Please enter the question text');
        return;
    }

    // Get max points from the UI
    const maxPointsInput = document.getElementById('max-points-input');
    const maxPoints = parseFloat(maxPointsInput.value) || 8; // Default to 8 if not set

    // Hide verify phase, show progress phase
    const verifyPhase = document.getElementById('autograding-verify-phase');
    const progressPhase = document.getElementById('autograding-progress-phase');
    verifyPhase.style.display = 'none';
    progressPhase.style.display = 'block';

    // Connect to SSE stream before starting
    connectToAutogradingStream();

    try {
        // Save rubric if provided
        if (rubricText.trim()) {
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

function connectToAutogradingStream() {
    const progressMessage = document.getElementById('autograding-progress-message');
    const progressBar = document.getElementById('autograding-progress-bar');
    const currentEl = document.getElementById('autograding-current');
    const totalEl = document.getElementById('autograding-total');

    // Close existing connection if any
    if (autogradingEventSource) {
        autogradingEventSource.close();
    }

    // Connect to SSE stream
    const streamUrl = `${API_BASE}/ai-grader/${currentSession.id}/autograde-stream`;
    autogradingEventSource = new EventSource(streamUrl);

    autogradingEventSource.addEventListener('connected', (e) => {
        console.log('SSE connected for autograding progress');
    });

    autogradingEventSource.addEventListener('start', (e) => {
        const data = JSON.parse(e.data);
        console.log('Autograding started:', data);
        progressMessage.textContent = data.message;
    });

    autogradingEventSource.addEventListener('progress', (e) => {
        const data = JSON.parse(e.data);
        console.log('Autograding progress:', data);

        progressMessage.textContent = data.message;
        progressBar.style.width = `${data.progress}%`;
        progressBar.textContent = `${data.progress}%`;
        currentEl.textContent = data.current;
        totalEl.textContent = data.total;
    });

    autogradingEventSource.addEventListener('complete', async (e) => {
        const data = JSON.parse(e.data);
        console.log('Autograding complete:', data);

        autogradingEventSource.close();
        autogradingEventSource = null;

        // Complete the progress bar
        progressBar.style.width = '100%';
        progressBar.textContent = '100%';
        progressMessage.textContent = data.message;

        // Close modal after a brief delay
        setTimeout(() => {
            const modal = document.getElementById('autograding-modal');
            modal.style.display = 'none';

            // Show completion message
            showNotification(`Autograding complete! ${data.graded} of ${data.total} problems graded. Please review the AI suggestions.`, async () => {
                // Reload current problem to show AI suggestion
                await loadProblemOrMostRecent();
            });
        }, 2000);
    });

    autogradingEventSource.addEventListener('error', (e) => {
        console.error('SSE error:', e);
        if (autogradingEventSource && autogradingEventSource.readyState === EventSource.CLOSED) {
            console.log('SSE connection closed');
            autogradingEventSource = null;
        } else {
            progressMessage.textContent = 'Connection error - autograding may still be running';
        }
    });
}

// =============================================================================
// PROBLEM IMAGE RESIZE FUNCTIONALITY
// =============================================================================

function setupProblemImageResize() {
    const scrollContainer = document.getElementById('problem-scroll-container');
    const resizeHandle = document.getElementById('problem-resize-handle');

    if (!scrollContainer || !resizeHandle) {
        console.warn('Problem scroll container or resize handle not found');
        return;
    }

    // Load saved height from localStorage
    const savedHeight = localStorage.getItem('problemScrollContainerHeight');
    if (savedHeight) {
        scrollContainer.style.height = savedHeight;
    }

    let isResizing = false;
    let startY = 0;
    let startHeight = 0;

    // Mouse down on resize handle
    resizeHandle.addEventListener('mousedown', (e) => {
        isResizing = true;
        startY = e.clientY;
        startHeight = scrollContainer.offsetHeight;

        // Prevent text selection during resize
        e.preventDefault();
        document.body.style.userSelect = 'none';
        document.body.style.cursor = 'ns-resize';
    });

    // Mouse move - resize
    document.addEventListener('mousemove', (e) => {
        if (!isResizing) return;

        const deltaY = e.clientY - startY;
        const newHeight = startHeight + deltaY;

        // Enforce minimum and maximum heights
        const minHeight = 200; // Minimum 200px
        const maxHeight = scrollContainer.dataset.maxImageHeight
            ? parseFloat(scrollContainer.dataset.maxImageHeight)
            : window.innerHeight * 0.9; // Fallback to 90% of viewport if not set

        if (newHeight >= minHeight && newHeight <= maxHeight) {
            scrollContainer.style.height = `${newHeight}px`;
        } else if (newHeight < minHeight) {
            scrollContainer.style.height = `${minHeight}px`;
        } else if (newHeight > maxHeight) {
            scrollContainer.style.height = `${maxHeight}px`;
        }
    });

    // Mouse up - stop resizing and save height
    document.addEventListener('mouseup', () => {
        if (isResizing) {
            isResizing = false;
            document.body.style.userSelect = '';
            document.body.style.cursor = '';

            // Save the height to localStorage
            const currentHeight = scrollContainer.style.height;
            localStorage.setItem('problemScrollContainerHeight', currentHeight);
            console.log('Saved problem container height:', currentHeight);
        }
    });
}

// =============================================================================
// STUDENT TABLE SORTING
// =============================================================================

let currentSortColumn = null;
let currentSortDirection = 'asc';

function sortStudentTable(column) {
    const tbody = document.getElementById('student-scores-tbody');
    if (!tbody) return;

    const rows = Array.from(tbody.querySelectorAll('tr'));

    // Toggle direction if clicking same column, otherwise default to ascending
    if (currentSortColumn === column) {
        currentSortDirection = currentSortDirection === 'asc' ? 'desc' : 'asc';
    } else {
        currentSortColumn = column;
        currentSortDirection = 'asc';
    }

    // Sort rows based on column and direction
    rows.sort((a, b) => {
        let aVal, bVal;

        if (column === 'name') {
            aVal = a.dataset.name.toLowerCase();
            bVal = b.dataset.name.toLowerCase();
            return currentSortDirection === 'asc'
                ? aVal.localeCompare(bVal)
                : bVal.localeCompare(aVal);
        } else if (column === 'progress') {
            aVal = parseFloat(a.dataset.progress);
            bVal = parseFloat(b.dataset.progress);
        } else if (column === 'score') {
            aVal = parseFloat(a.dataset.score);
            bVal = parseFloat(b.dataset.score);
        }

        // Numerical comparison
        if (currentSortDirection === 'asc') {
            return aVal - bVal;
        } else {
            return bVal - aVal;
        }
    });

    // Re-append rows in sorted order
    rows.forEach(row => tbody.appendChild(row));

    // Update sort indicators
    updateSortIndicators(column);
}

function updateSortIndicators(column) {
    // Clear all indicators
    document.querySelectorAll('.student-scores-table th .sort-indicator').forEach(indicator => {
        indicator.textContent = '';
    });

    // Set current indicator
    const th = document.querySelector(`.student-scores-table th[data-sort="${column}"]`);
    if (th) {
        const indicator = th.querySelector('.sort-indicator');
        if (indicator) {
            indicator.textContent = currentSortDirection === 'asc' ? ' ▲' : ' ▼';
        }
    }
}

// =============================================================================
// EXPLANATION LOADING AND AUTO-INCLUDE IN FEEDBACK
// =============================================================================

// Cache for explanations { problemId: markdownText }
let explanationCache = {};

// Load explanation from QR code if available
async function loadExplanation() {
    const container = document.getElementById('explanation-container');
    const content = document.getElementById('explanation-content');

    if (!currentProblem || !currentProblem.has_qr_data) {
        // No QR data - hide explanation container
        container.style.display = 'none';
        return;
    }

    // Check cache first
    if (explanationCache[currentProblem.id]) {
        content.innerHTML = explanationCache[currentProblem.id];
        container.style.display = 'block';
        return;
    }

    // Show loading state
    container.style.display = 'block';
    content.innerHTML = '<span class="explanation-placeholder">Loading explanation...</span>';

    try {
        const response = await fetch(`${API_BASE}/problems/${currentProblem.id}/regenerate-answer`);

        if (!response.ok) {
            // Failed to load - hide container
            container.style.display = 'none';
            return;
        }

        const data = await response.json();

        if (data.explanation_markdown) {
            // Cache and display explanation
            explanationCache[currentProblem.id] = data.explanation_markdown;
            content.innerHTML = data.explanation_markdown;

            // Render MathJax if available
            if (typeof MathJax !== 'undefined') {
                MathJax.typesetPromise([content]).catch((err) => console.error('MathJax typesetting failed:', err));
            }
        } else {
            // No explanation available
            container.style.display = 'none';
        }

    } catch (error) {
        console.error('Failed to load explanation:', error);
        container.style.display = 'none';
    }
}
