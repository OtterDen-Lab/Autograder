// Main application logic

const API_BASE = '/api';
let currentSession = null;

// Initialize app
document.addEventListener('DOMContentLoaded', () => {
    loadSessions();
    setupEventListeners();
});

// Get status badge HTML with color coding
function getStatusBadge(status) {
    const statusConfig = {
        'preprocessing': { label: 'Processing', color: '#3b82f6' },  // blue
        'name_matching_needed': { label: 'Needs Matching', color: '#f59e0b' },  // amber
        'ready': { label: 'Ready to Grade', color: '#10b981' },  // green
        'grading': { label: 'Grading', color: '#8b5cf6' },  // purple
        'finalizing': { label: 'Finalizing', color: '#ec4899' },  // pink
        'finalized': { label: 'Finalized', color: '#6b7280' },  // grey
        'complete': { label: 'Complete', color: '#059669' },  // dark green (legacy)
        'error': { label: 'Error', color: '#ef4444' }  // red
    };

    const config = statusConfig[status] || { label: status, color: '#6b7280' };
    return `<span class="status-badge" style="background-color: ${config.color}; color: white; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 600;">${config.label}</span>`;
}

// Load existing sessions
async function loadSessions() {
    try {
        const response = await fetch(`${API_BASE}/sessions`);
        const sessions = await response.json();

        const sessionList = document.getElementById('session-list');
        sessionList.innerHTML = '';

        sessions.forEach(session => {
            const item = document.createElement('div');
            item.className = 'session-item';

            // Get status badge HTML
            const statusBadge = getStatusBadge(session.status);
            const statusMessage = session.processing_message ? `<div class="session-status-message">${session.processing_message}</div>` : '';

            item.innerHTML = `
                <div class="session-item-content">
                    <div class="session-item-main">
                        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 5px;">
                            <strong>${session.assignment_name}</strong>
                            ${statusBadge}
                        </div>
                        <div>${session.course_name || `Course ${session.course_id}`}</div>
                        ${statusMessage}
                    </div>
                    <button class="btn btn-danger btn-small" onclick="event.stopPropagation(); deleteSession(${session.id})">Delete</button>
                </div>
            `;
            item.onclick = () => selectSession(session.id);
            sessionList.appendChild(item);
        });
    } catch (error) {
        console.error('Failed to load sessions:', error);
    }
}

// Select a session
async function selectSession(sessionId) {
    try {
        const response = await fetch(`${API_BASE}/sessions/${sessionId}`);
        currentSession = await response.json();

        updateSessionInfo();
        navigateToSection(getNextSectionForStatus(currentSession.status));
    } catch (error) {
        console.error('Failed to select session:', error);
    }
}

// Update session info in header
function updateSessionInfo() {
    const info = document.getElementById('session-info');
    const homeBtn = document.getElementById('home-btn');

    if (currentSession) {
        info.innerHTML = `
            ${currentSession.assignment_name} - ${currentSession.course_name || `Course ${currentSession.course_id}`}
            <span style="margin-left: 20px;">Status: ${currentSession.status}</span>
        `;
        homeBtn.style.display = 'block';  // Show home button when session is active
    } else {
        info.innerHTML = '';
        homeBtn.style.display = 'none';
    }
}

// Navigate to appropriate section based on status
function getNextSectionForStatus(status) {
    const sectionMap = {
        'preprocessing': 'upload-section',
        'name_matching_needed': 'matching-section',
        'ready': 'grading-section',
        'grading': 'grading-section',
        'finalizing': 'stats-section',
        'finalized': 'stats-section',
        'complete': 'stats-section',
        'error': 'stats-section'
    };
    return sectionMap[status] || 'upload-section';
}

// Navigate between sections
function navigateToSection(sectionId) {
    document.querySelectorAll('.section').forEach(section => {
        section.classList.remove('active');
    });
    document.getElementById(sectionId).classList.add('active');
}

// Setup event listeners
function setupEventListeners() {
    // Home button - go back to session selection
    document.getElementById('home-btn').onclick = () => {
        currentSession = null;
        document.getElementById('session-info').innerHTML = '';
        document.getElementById('home-btn').style.display = 'none';
        navigateToSection('session-section');
        loadSessions();
    };

    // New session button - toggle form
    document.getElementById('new-session-btn').onclick = () => {
        const form = document.getElementById('new-session-form');
        const btn = document.getElementById('new-session-btn');
        if (form.style.display === 'none') {
            form.style.display = 'block';
            btn.textContent = '− Hide Form';
            const useProd = document.getElementById('canvas-env-new').value === 'true';
            loadCourses(useProd); // Load courses when form is shown
        } else {
            form.style.display = 'none';
            btn.textContent = '+ Create New Session';
        }
    };

    // Canvas environment change handler
    document.getElementById('canvas-env-new').onchange = (e) => {
        const useProd = e.target.value === 'true';
        loadCourses(useProd);
        // Clear assignment selection when environment changes
        document.getElementById('assignment-select').innerHTML = '<option value="">Select a course first</option>';
        document.getElementById('assignment-select').disabled = true;
        document.getElementById('assignment-info').textContent = '';
    };

    // Import session button
    document.getElementById('import-session-btn').onclick = () => {
        document.getElementById('import-file-input').click();
    };

    document.getElementById('import-file-input').onchange = async (e) => {
        const file = e.target.files[0];
        if (!file) return;

        try {
            // Create FormData and append file
            const formData = new FormData();
            formData.append('file', file);

            const response = await fetch(`${API_BASE}/sessions/import`, {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Import failed');
            }

            const result = await response.json();
            alert(`Successfully imported session: ${result.assignment_name}\n${result.submissions_imported} submissions imported`);

            // Reload sessions and select the new one
            await loadSessions();
            await selectSession(result.session_id);

        } catch (error) {
            console.error('Import failed:', error);
            alert(`Import failed: ${error.message}`);
        } finally {
            // Reset file input
            e.target.value = '';
        }
    };

    // Course selection handler
    document.getElementById('course-select').onchange = async (e) => {
        const courseId = e.target.value;
        const infoBox = document.getElementById('course-info');

        if (!courseId) {
            infoBox.textContent = '';
            infoBox.className = 'info-box';
            return;
        }

        const selectedOption = e.target.options[e.target.selectedIndex];
        const courseName = selectedOption.textContent;

        infoBox.textContent = `✓ Selected: ${courseName}`;
        infoBox.className = 'info-box success';

        // Load assignments for this course
        const useProd = document.getElementById('canvas-env-new').value === 'true';
        await loadAssignments(parseInt(courseId), useProd);
    };

    // Assignment selection handler
    document.getElementById('assignment-select').onchange = (e) => {
        const assignmentId = e.target.value;
        const infoBox = document.getElementById('assignment-info');

        if (!assignmentId) {
            infoBox.textContent = '';
            infoBox.className = 'info-box';
            return;
        }

        const selectedOption = e.target.options[e.target.selectedIndex];
        const assignmentName = selectedOption.textContent;
        const points = selectedOption.dataset.points;

        // Auto-populate points if available
        if (points && points !== 'null') {
            document.getElementById('canvas-points-input').value = points;
        }

        infoBox.textContent = `✓ Selected: ${assignmentName} (${points || '?'} points)`;
        infoBox.className = 'info-box success';
    };

    // Session form submission
    document.getElementById('session-form').onsubmit = createNewSession;

    // Cancel button
    document.getElementById('cancel-session-btn').onclick = () => {
        document.getElementById('new-session-form').style.display = 'none';
        document.getElementById('new-session-btn').textContent = '+ Create New Session';
        document.getElementById('session-form').reset();
    };

    // Upload area
    const uploadArea = document.getElementById('upload-area');
    const fileInput = document.getElementById('file-input');

    uploadArea.onclick = () => fileInput.click();

    uploadArea.ondragover = (e) => {
        e.preventDefault();
        uploadArea.style.borderColor = 'var(--primary-color)';
    };

    uploadArea.ondragleave = () => {
        uploadArea.style.borderColor = 'var(--gray-200)';
    };

    uploadArea.ondrop = (e) => {
        e.preventDefault();
        uploadArea.style.borderColor = 'var(--gray-200)';
        fileInput.files = e.dataTransfer.files;
        uploadFiles();
    };

    fileInput.onchange = uploadFiles;
}

// Load courses from Canvas
async function loadCourses(useProd = false) {
    const courseSelect = document.getElementById('course-select');
    const infoBox = document.getElementById('course-info');

    courseSelect.innerHTML = '<option value="">Loading courses...</option>';
    courseSelect.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/canvas/courses?use_prod=${useProd}`);
        if (!response.ok) {
            throw new Error('Failed to load courses');
        }

        const data = await response.json();

        courseSelect.innerHTML = '<option value="">-- Select a Course --</option>';
        data.courses.forEach(course => {
            const option = document.createElement('option');
            option.value = course.id;
            // Add star indicator for favorite courses
            const prefix = course.is_favorite ? '⭐ ' : '';
            option.textContent = prefix + course.name;
            courseSelect.appendChild(option);
        });

        courseSelect.disabled = false;
        infoBox.textContent = `Loaded ${data.courses.length} courses from Canvas ${data.environment}`;
        infoBox.className = 'info-box success';

    } catch (error) {
        console.error('Failed to load courses:', error);
        courseSelect.innerHTML = '<option value="">Failed to load courses</option>';
        infoBox.textContent = 'Failed to load courses from Canvas';
        infoBox.className = 'info-box error';
    }
}

// Load assignments for a course
async function loadAssignments(courseId, useProd = false) {
    const assignmentSelect = document.getElementById('assignment-select');
    const infoBox = document.getElementById('assignment-info');

    assignmentSelect.innerHTML = '<option value="">Loading assignments...</option>';
    assignmentSelect.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/canvas/courses/${courseId}/assignments?use_prod=${useProd}`);
        if (!response.ok) {
            throw new Error('Failed to load assignments');
        }

        const data = await response.json();

        assignmentSelect.innerHTML = '<option value="">-- Select an Assignment --</option>';
        data.assignments.forEach(assignment => {
            const option = document.createElement('option');
            option.value = assignment.id;
            option.textContent = assignment.name;
            option.dataset.points = assignment.points_possible;
            assignmentSelect.appendChild(option);
        });

        assignmentSelect.disabled = false;
        infoBox.textContent = `Loaded ${data.assignments.length} assignments`;
        infoBox.className = 'info-box success';

    } catch (error) {
        console.error('Failed to load assignments:', error);
        assignmentSelect.innerHTML = '<option value="">Failed to load assignments</option>';
        infoBox.textContent = 'Failed to load assignments';
        infoBox.className = 'info-box error';
    }
}

// Create new session
async function createNewSession(e) {
    e.preventDefault();

    const courseSelect = document.getElementById('course-select');
    const assignmentSelect = document.getElementById('assignment-select');

    const courseId = parseInt(courseSelect.value);
    const assignmentId = parseInt(assignmentSelect.value);

    // Get names from selected options
    const courseName = courseSelect.options[courseSelect.selectedIndex].textContent;
    const assignmentName = assignmentSelect.options[assignmentSelect.selectedIndex].textContent;

    // Get points (either override or from assignment)
    const pointsInput = document.getElementById('canvas-points-input').value;
    const assignmentPoints = assignmentSelect.options[assignmentSelect.selectedIndex].dataset.points;
    const canvasPoints = pointsInput ? parseFloat(pointsInput) : (assignmentPoints ? parseFloat(assignmentPoints) : null);

    // Get environment setting
    const useProdCanvas = document.getElementById('canvas-env-new').value === 'true';

    try {
        const response = await fetch(`${API_BASE}/sessions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                course_id: courseId,
                assignment_id: assignmentId,
                assignment_name: assignmentName,
                course_name: courseName,
                canvas_points: canvasPoints,
                use_prod_canvas: useProdCanvas
            })
        });

        currentSession = await response.json();

        // Hide form and reset
        document.getElementById('new-session-form').style.display = 'none';
        document.getElementById('new-session-btn').textContent = '+ Create New Session';
        document.getElementById('session-form').reset();

        updateSessionInfo();
        navigateToSection('upload-section');
    } catch (error) {
        console.error('Failed to create session:', error);
        alert('Failed to create session');
    }
}

// Upload files
async function uploadFiles() {
    const fileInput = document.getElementById('file-input');
    if (!fileInput.files.length || !currentSession) return;

    const formData = new FormData();
    for (const file of fileInput.files) {
        formData.append('files', file);
    }

    try {
        // Connect to SSE stream BEFORE uploading so we don't miss any events
        listenForStatusUpdates();

        // Now upload the files
        const response = await fetch(`${API_BASE}/uploads/${currentSession.id}/upload`, {
            method: 'POST',
            body: formData
        });

        const result = await response.json();
        document.getElementById('upload-status').textContent = result.message;

    } catch (error) {
        console.error('Upload failed:', error);
        alert('Upload failed');
        // Close SSE connection on error
        if (uploadEventSource) {
            uploadEventSource.close();
            uploadEventSource = null;
        }
    }
}

// Delete a session
async function deleteSession(sessionId) {
    if (!confirm('Are you sure you want to delete this session? This cannot be undone.')) {
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/sessions/${sessionId}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            // Reload sessions list
            loadSessions();
        } else {
            alert('Failed to delete session');
        }
    } catch (error) {
        console.error('Failed to delete session:', error);
        alert('Failed to delete session');
    }
}

// Listen for status updates via SSE
let uploadEventSource = null;

function listenForStatusUpdates() {
    const container = document.getElementById('upload-progress-container');
    const progressFill = document.getElementById('upload-progress-fill');
    const statusDiv = document.getElementById('upload-status');

    // Show progress container
    container.style.display = 'block';
    statusDiv.textContent = 'Starting upload processing...';
    progressFill.style.width = '0%';

    console.log('Started listening for status updates via SSE');

    // Close existing connection if any
    if (uploadEventSource) {
        uploadEventSource.close();
    }

    // Connect to SSE stream
    const streamUrl = `${API_BASE}/uploads/${currentSession.id}/upload-stream`;
    uploadEventSource = new EventSource(streamUrl);

    uploadEventSource.addEventListener('connected', (e) => {
        console.log('SSE connected for upload progress');
    });

    uploadEventSource.addEventListener('progress', (e) => {
        const data = JSON.parse(e.data);
        console.log('Upload progress:', data);

        statusDiv.textContent = data.message;
        progressFill.style.width = `${data.progress}%`;
        progressFill.textContent = `${data.progress}%`;
    });

    uploadEventSource.addEventListener('complete', async (e) => {
        const data = JSON.parse(e.data);
        console.log('Upload complete:', data);

        uploadEventSource.close();
        uploadEventSource = null;

        // Complete the progress bar
        progressFill.style.width = '100%';
        progressFill.textContent = '100%';
        statusDiv.textContent = data.message;

        // Reload session info
        const response = await fetch(`${API_BASE}/sessions/${currentSession.id}`);
        currentSession = await response.json();
        updateSessionInfo();

        // Show final message for 2 seconds before navigating
        setTimeout(() => {
            navigateToSection(getNextSectionForStatus(currentSession.status));
        }, 2000);
    });

    uploadEventSource.addEventListener('error', (e) => {
        console.error('SSE error:', e);
        if (uploadEventSource && uploadEventSource.readyState === EventSource.CLOSED) {
            console.log('SSE connection closed');
            uploadEventSource = null;
        } else {
            statusDiv.textContent = 'Connection error - please refresh';
        }
    });
}
