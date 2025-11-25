// Name matching functionality

let allSubmissions = [];
let allStudents = [];

// Simple fuzzy matching helper (Levenshtein distance)
function fuzzyMatch(str1, str2) {
    const s1 = str1.toLowerCase();
    const s2 = str2.toLowerCase();
    const len1 = s1.length;
    const len2 = s2.length;

    const matrix = [];
    for (let i = 0; i <= len1; i++) {
        matrix[i] = [i];
    }
    for (let j = 0; j <= len2; j++) {
        matrix[0][j] = j;
    }

    for (let i = 1; i <= len1; i++) {
        for (let j = 1; j <= len2; j++) {
            const cost = s1[i - 1] === s2[j - 1] ? 0 : 1;
            matrix[i][j] = Math.min(
                matrix[i - 1][j] + 1,
                matrix[i][j - 1] + 1,
                matrix[i - 1][j - 1] + cost
            );
        }
    }

    const maxLen = Math.max(len1, len2);
    const distance = matrix[len1][len2];
    return Math.round((1 - distance / maxLen) * 100);
}

// Load name matching interface
async function loadNameMatching() {
    if (!currentSession) return;

    try {
        // Fetch all submissions (unmatched first)
        const submissionsResp = await fetch(`${API_BASE}/matching/${currentSession.id}/submissions`);
        const submissionsData = await submissionsResp.json();
        allSubmissions = submissionsData.submissions;

        // Fetch all students (unmatched first)
        const studentsResp = await fetch(`${API_BASE}/matching/${currentSession.id}/students`);
        const studentsData = await studentsResp.json();
        allStudents = studentsData.students;

        // Pre-fill suggested matches based on fuzzy matching
        // Only if not already matched
        allSubmissions.forEach(submission => {
            if (!submission.canvas_user_id && submission.approximate_name) {
                let bestScore = 0;
                let bestStudent = null;

                allStudents.forEach(student => {
                    const score = fuzzyMatch(submission.approximate_name, student.name);
                    if (score > bestScore && score >= 97) {  // 97% threshold (same as backend)
                        bestScore = score;
                        bestStudent = student;
                    }
                });

                if (bestStudent) {
                    submission.suggested_canvas_user_id = bestStudent.user_id;
                    console.log(`Suggested match for "${submission.approximate_name}": ${bestStudent.name} (${bestScore}%)`);
                }
            }
        });

        // Render UI
        renderMatchingList();

    } catch (error) {
        console.error('Failed to load matching data:', error);
    }
}

// Render all submissions list
function renderMatchingList() {
    const container = document.getElementById('unmatched-list');

    const unmatchedCount = allSubmissions.filter(s => !s.is_matched).length;
    const matchedCount = allSubmissions.length - unmatchedCount;
    const percentage = allSubmissions.length > 0 ? (matchedCount / allSubmissions.length * 100) : 0;

    // Update progress bar
    document.getElementById('matching-progress-fill').style.width = `${percentage}%`;
    document.getElementById('matching-progress-text').textContent =
        `${matchedCount} of ${allSubmissions.length} matched (${unmatchedCount} remaining)`;

    let html = `
        <p style="margin-bottom: 20px;">
            <strong>${unmatchedCount}</strong> of <strong>${allSubmissions.length}</strong> submission(s) need manual matching.
        </p>
        <div style="margin-bottom: 20px; text-align: center;">
            <button id="confirm-all-matches-btn" class="btn btn-primary" onclick="confirmAllMatches()" style="padding: 10px 30px; font-size: 16px;">
                Confirm All Matches
            </button>
            <p style="margin-top: 10px; color: var(--gray-600); font-size: 14px;">
                Select students from the dropdowns below, then click this button to confirm all changes at once.
            </p>
        </div>
    `;

    allSubmissions.forEach(submission => {
        const statusClass = submission.is_matched ? 'matched' : 'unmatched';
        const statusLabel = submission.is_matched ? `✓ Matched to: ${submission.student_name}` : 'Not matched';

        html += `
            <div class="matching-item ${statusClass}" data-submission-id="${submission.id}">
                <div class="matching-info">
                    <div style="display: flex; gap: 15px; align-items: flex-start;">
                        ${submission.name_image_data ? `
                            <img src="data:image/png;base64,${submission.name_image_data}"
                                 alt="Name area"
                                 style="max-width: 200px; border: 1px solid #ccc; border-radius: 4px;">
                        ` : ''}
                        <div style="flex: 1;">
                            <strong>Exam #${submission.document_id + 1}</strong>
                            <div class="detected-name">AI detected: <em>${submission.approximate_name}</em></div>
                            <div class="match-status">${statusLabel}</div>
                        </div>
                    </div>
                </div>
                <div class="matching-control">
                    <select class="student-select" id="select-${submission.id}"
                            ${submission.is_matched ? `data-current-match="${submission.canvas_user_id}"` : ''}
                            onchange="handleStudentSelection(${submission.id})">
                        <option value="">-- Select Canvas Student --</option>
                        ${allStudents.map(s => {
                            // Pre-select if this is the actual match OR the suggested match
                            const isSelected = (submission.canvas_user_id === s.user_id) ||
                                             (!submission.canvas_user_id && submission.suggested_canvas_user_id === s.user_id);
                            return `
                            <option value="${s.user_id}"
                                    ${s.is_matched ? 'class="matched-student"' : ''}
                                    ${isSelected ? 'selected' : ''}>
                                ${s.is_matched ? '✓ ' : ''}${s.name}
                            </option>
                        `;
                        }).join('')}
                    </select>
                </div>
            </div>
        `;
    });

    container.innerHTML = html;
}

// Handle student selection - show warning if student is already matched
function handleStudentSelection(submissionId) {
    const select = document.getElementById(`select-${submissionId}`);
    const selectedUserId = parseInt(select.value);

    if (!selectedUserId) return;

    // Find the selected student
    const student = allStudents.find(s => s.user_id === selectedUserId);

    // Check if this student is already matched
    if (student && student.is_matched) {
        const currentMatchId = select.dataset.currentMatch;

        // Only show warning if reassigning to a different student
        if (!currentMatchId || parseInt(currentMatchId) !== selectedUserId) {
            select.style.borderColor = '#ef4444';
            select.style.backgroundColor = '#fee2e2';
        }
    } else {
        select.style.borderColor = '';
        select.style.backgroundColor = '';
    }
}

// Confirm all matches at once (batch operation)
async function confirmAllMatches() {
    // Collect all pending matches
    const pendingMatches = [];
    const warnings = [];

    for (const submission of allSubmissions) {
        const select = document.getElementById(`select-${submission.id}`);
        const selectedUserId = parseInt(select.value);

        // Skip if no selection or if already matched to the same student
        if (!selectedUserId) continue;
        if (submission.is_matched && submission.canvas_user_id === selectedUserId) continue;

        // Check for warnings (reassignments)
        const student = allStudents.find(s => s.user_id === selectedUserId);
        if (student && student.is_matched) {
            const currentMatchId = select.dataset.currentMatch;
            if (!currentMatchId || parseInt(currentMatchId) !== selectedUserId) {
                warnings.push(`"${student.name}" will be reassigned to Exam #${submission.document_id + 1}`);
            }
        }

        pendingMatches.push({
            submission_id: submission.id,
            canvas_user_id: selectedUserId,
            exam_number: submission.document_id + 1
        });
    }

    // Check if all submissions are already matched (even if no pending changes)
    const unmatchedCount = allSubmissions.filter(s => !s.is_matched).length;

    if (pendingMatches.length === 0) {
        // No pending changes, but check if we should update status
        if (unmatchedCount === 0) {
            // All already matched - just update status to 'ready' and navigate
            console.log('All submissions already matched. Updating status to ready...');

            try {
                const statusResponse = await fetch(`${API_BASE}/sessions/${currentSession.id}/status`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status: 'ready' })
                });

                if (!statusResponse.ok) {
                    let errorMessage = `HTTP ${statusResponse.status}`;
                    try {
                        const errorData = await statusResponse.json();
                        errorMessage = errorData.detail || JSON.stringify(errorData);
                    } catch (e) {
                        errorMessage = statusResponse.statusText || errorMessage;
                    }
                    alert(`Failed to update status: ${errorMessage}`);
                    return;
                }

                console.log('Status successfully updated to ready');

                // Reload session and navigate
                const response = await fetch(`${API_BASE}/sessions/${currentSession.id}`);
                currentSession = await response.json();
                console.log(`Session reloaded. Current status: ${currentSession.status}`);
                updateSessionInfo();
                navigateToSection('grading-section');
            } catch (error) {
                console.error('Failed to update status:', error);
                alert('Failed to update status: ' + error.message);
            }
            return;
        } else {
            alert('No new matches to confirm. Please select students from the dropdowns.');
            return;
        }
    }

    // Show confirmation dialog with warnings if any
    let confirmMessage = `Confirm ${pendingMatches.length} match(es)?`;
    if (warnings.length > 0) {
        confirmMessage += '\n\nWarnings:\n' + warnings.join('\n');
    }

    if (!confirm(confirmMessage)) {
        return;
    }

    // Disable button during processing
    const btn = document.getElementById('confirm-all-matches-btn');
    btn.disabled = true;
    btn.textContent = 'Processing...';

    try {
        // Process all matches
        let successCount = 0;
        let failCount = 0;

        for (const match of pendingMatches) {
            try {
                const response = await fetch(`${API_BASE}/matching/${currentSession.id}/match`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        submission_id: match.submission_id,
                        canvas_user_id: match.canvas_user_id
                    })
                });

                if (response.ok) {
                    successCount++;
                } else {
                    failCount++;
                    console.error(`Failed to match submission ${match.submission_id}`);
                }
            } catch (error) {
                failCount++;
                console.error(`Error matching submission ${match.submission_id}:`, error);
            }
        }

        // Show result
        if (failCount > 0) {
            alert(`Completed with ${successCount} successful and ${failCount} failed matches.`);
        }

        // Reload data to reflect changes
        await loadNameMatching();

        // Check if all submissions are matched, then set status to 'ready'
        const unmatchedCount = allSubmissions.filter(s => !s.is_matched).length;

        if (unmatchedCount === 0) {
            // All matched - update session status to 'ready'
            console.log(`All ${allSubmissions.length} submissions matched. Updating status to 'ready'...`);
            const statusResponse = await fetch(`${API_BASE}/sessions/${currentSession.id}/status`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status: 'ready' })
            });

            if (!statusResponse.ok) {
                let errorMessage = `HTTP ${statusResponse.status}`;
                try {
                    const errorData = await statusResponse.json();
                    errorMessage = errorData.detail || JSON.stringify(errorData);
                } catch (e) {
                    errorMessage = statusResponse.statusText || errorMessage;
                }
                throw new Error(`Failed to update status: ${errorMessage}`);
            }

            console.log('Status successfully updated to ready');

            // Reload session and navigate
            const response = await fetch(`${API_BASE}/sessions/${currentSession.id}`);
            currentSession = await response.json();
            console.log(`Session reloaded. Current status: ${currentSession.status}`);
            updateSessionInfo();
            navigateToSection('grading-section');
        } else {
            // Some submissions still unmatched
            console.log(`${unmatchedCount} submissions still need matching`);
            alert(`${unmatchedCount} submission(s) still need to be matched. Please select students for all submissions.`);
        }

    } catch (error) {
        console.error('Failed to confirm matches:', error);
        alert('Failed to confirm matches: ' + error.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Confirm All Matches';
    }
}

// Match a submission to a student (legacy single-match function, kept for compatibility)
async function matchSubmission(submissionId) {
    const select = document.getElementById(`select-${submissionId}`);
    const canvasUserId = parseInt(select.value);

    if (!canvasUserId) {
        alert('Please select a student');
        return;
    }

    // Find the selected student
    const student = allStudents.find(s => s.user_id === canvasUserId);

    // Confirm if reassigning
    if (student && student.is_matched) {
        const currentMatchId = select.dataset.currentMatch;
        if (!currentMatchId || parseInt(currentMatchId) !== canvasUserId) {
            if (!confirm(`"${student.name}" is already matched to another exam. This will unassign them from that exam and assign them to this one. Continue?`)) {
                return;
            }
        }
    }

    try {
        const response = await fetch(`${API_BASE}/matching/${currentSession.id}/match`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                submission_id: submissionId,
                canvas_user_id: canvasUserId
            })
        });

        const result = await response.json();

        // Reload data to reflect changes
        await loadNameMatching();

        // If all matched, navigate to grading
        if (result.remaining_unmatched === 0) {
            setTimeout(() => {
                currentSession.status = 'ready';
                updateSessionInfo();
                navigateToSection('grading-section');
            }, 1500);
        }

    } catch (error) {
        console.error('Failed to match submission:', error);
        alert('Failed to match submission');
    }
}

// Auto-load data when navigating to sections
document.addEventListener('DOMContentLoaded', () => {
    const originalNavigate = window.navigateToSection;
    window.navigateToSection = function(sectionId) {
        originalNavigate(sectionId);
        if (sectionId === 'matching-section') {
            loadNameMatching();
        } else if (sectionId === 'grading-section') {
            initializeGrading();
        } else if (sectionId === 'stats-section') {
            loadStatistics();
            // Check if finalization is in progress
            if (currentSession && currentSession.status === 'finalizing') {
                document.getElementById('finalization-progress').style.display = 'block';
                document.getElementById('finalize-btn').disabled = true;
                startFinalizationPolling();
            }
        }
    };
});
