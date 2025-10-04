// Name matching functionality

let allSubmissions = [];
let allStudents = [];

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
                        ${allStudents.map(s => `
                            <option value="${s.user_id}"
                                    ${s.is_matched ? 'class="matched-student"' : ''}
                                    ${submission.canvas_user_id === s.user_id ? 'selected' : ''}>
                                ${s.is_matched ? '✓ ' : ''}${s.name}
                            </option>
                        `).join('')}
                    </select>
                    <button class="btn btn-primary btn-small" onclick="matchSubmission(${submission.id})">
                        ${submission.is_matched ? 'Reassign' : 'Match'}
                    </button>
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

// Match a submission to a student
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
