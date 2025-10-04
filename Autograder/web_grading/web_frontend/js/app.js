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
        'complete': { label: 'Complete', color: '#059669' },  // dark green
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
        } else {
            form.style.display = 'none';
            btn.textContent = '+ Create New Session';
        }
    };

    // Session form submission
    document.getElementById('session-form').onsubmit = createNewSession;

    // Cancel button
    document.getElementById('cancel-session-btn').onclick = () => {
        document.getElementById('new-session-form').style.display = 'none';
        document.getElementById('new-session-btn').textContent = '+ Create New Session';
        document.getElementById('session-form').reset();
    };

    // Fetch course info button
    document.getElementById('fetch-course-btn').onclick = fetchCourseInfo;

    // Fetch assignment info button
    document.getElementById('fetch-assignment-btn').onclick = fetchAssignmentInfo;

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

// Fetch course info from Canvas
async function fetchCourseInfo() {
    const courseId = document.getElementById('course-id-input').value;
    const infoBox = document.getElementById('course-info');

    if (!courseId) {
        infoBox.textContent = 'Please enter a course ID';
        infoBox.className = 'info-box error';
        return;
    }

    infoBox.textContent = 'Fetching course info...';
    infoBox.className = 'info-box';

    try {
        const response = await fetch(`${API_BASE}/canvas/courses/${courseId}`);
        if (response.ok) {
            const course = await response.json();
            document.getElementById('course-name-input').value = course.name;
            const env = course.environment ? ` (${course.environment})` : '';
            infoBox.textContent = `✓ Found: ${course.name}${env}`;
            infoBox.className = 'info-box success';
        } else {
            infoBox.textContent = 'Course not found';
            infoBox.className = 'info-box error';
        }
    } catch (error) {
        console.error('Failed to fetch course:', error);
        infoBox.textContent = 'Failed to fetch course info (API not implemented yet)';
        infoBox.className = 'info-box error';
    }
}

// Fetch assignment info from Canvas
async function fetchAssignmentInfo() {
    const courseId = document.getElementById('course-id-input').value;
    const assignmentId = document.getElementById('assignment-id-input').value;
    const infoBox = document.getElementById('assignment-info');

    if (!courseId || !assignmentId) {
        infoBox.textContent = 'Please enter both course ID and assignment ID';
        infoBox.className = 'info-box error';
        return;
    }

    infoBox.textContent = 'Fetching assignment info...';
    infoBox.className = 'info-box';

    try {
        const response = await fetch(`${API_BASE}/canvas/courses/${courseId}/assignments/${assignmentId}`);
        if (response.ok) {
            const assignment = await response.json();
            document.getElementById('assignment-name-input').value = assignment.name;
            if (assignment.points_possible) {
                document.getElementById('canvas-points-input').value = assignment.points_possible;
            }
            const env = assignment.environment ? ` (${assignment.environment})` : '';
            infoBox.textContent = `✓ Found: ${assignment.name} (${assignment.points_possible} points)${env}`;
            infoBox.className = 'info-box success';
        } else {
            infoBox.textContent = 'Assignment not found';
            infoBox.className = 'info-box error';
        }
    } catch (error) {
        console.error('Failed to fetch assignment:', error);
        infoBox.textContent = 'Failed to fetch assignment info (API not implemented yet)';
        infoBox.className = 'info-box error';
    }
}

// Create new session
async function createNewSession(e) {
    e.preventDefault();

    const courseId = parseInt(document.getElementById('course-id-input').value);
    const assignmentId = parseInt(document.getElementById('assignment-id-input').value);
    const assignmentName = document.getElementById('assignment-name-input').value;
    const courseName = document.getElementById('course-name-input').value;
    const canvasPoints = parseFloat(document.getElementById('canvas-points-input').value) || null;

    try {
        const response = await fetch(`${API_BASE}/sessions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                course_id: courseId,
                assignment_id: assignmentId,
                assignment_name: assignmentName,
                course_name: courseName || null,
                canvas_points: canvasPoints
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
        const response = await fetch(`${API_BASE}/uploads/${currentSession.id}/upload`, {
            method: 'POST',
            body: formData
        });

        const result = await response.json();
        document.getElementById('upload-status').textContent = result.message;

        // Start listening for status updates
        listenForStatusUpdates();
    } catch (error) {
        console.error('Upload failed:', error);
        alert('Upload failed');
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

// Listen for status updates via polling (SSE to be implemented)
function listenForStatusUpdates() {
    const container = document.getElementById('upload-progress-container');
    const progressFill = document.getElementById('upload-progress-fill');
    const statusDiv = document.getElementById('upload-status');

    // Show progress container
    container.style.display = 'block';
    statusDiv.textContent = 'Starting upload processing...';
    progressFill.style.width = '0%';

    console.log('Started listening for status updates');

    const interval = setInterval(async () => {
        try {
            const response = await fetch(`${API_BASE}/sessions/${currentSession.id}`);
            const session = await response.json();

            console.log('Status update:', session.processing_message, session.status);

            // Update progress display
            if (session.processing_message) {
                statusDiv.textContent = session.processing_message;

                // Try to parse progress from message (e.g., "Processing exam 3/27")
                const progressMatch = session.processing_message.match(/(\d+)\/(\d+)/);
                if (progressMatch) {
                    const current = parseInt(progressMatch[1]);
                    const total = parseInt(progressMatch[2]);
                    const percentage = Math.round((current / total) * 100);
                    progressFill.style.width = `${percentage}%`;
                    progressFill.textContent = `${percentage}%`;
                }
            }

            if (session.status !== currentSession.status) {
                currentSession = session;
                updateSessionInfo();

                if (session.status === 'ready' || session.status === 'name_matching_needed') {
                    clearInterval(interval);

                    // Complete the progress bar
                    progressFill.style.width = '100%';
                    progressFill.textContent = '100%';
                    statusDiv.textContent = 'Processing complete!';

                    // Show final message for 2 seconds before navigating
                    setTimeout(() => {
                        navigateToSection(getNextSectionForStatus(session.status));
                    }, 2000);
                }
            }
        } catch (error) {
            console.error('Status check failed:', error);
        }
    }, 500);  // Poll every 500ms
}
