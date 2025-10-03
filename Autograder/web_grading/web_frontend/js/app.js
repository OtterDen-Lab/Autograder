// Main application logic

const API_BASE = '/api';
let currentSession = null;

// Initialize app
document.addEventListener('DOMContentLoaded', () => {
    loadSessions();
    setupEventListeners();
});

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
            item.innerHTML = `
                <div class="session-item-content">
                    <div class="session-item-main">
                        <strong>${session.assignment_name}</strong>
                        <div>${session.course_name || `Course ${session.course_id}`}</div>
                        <div>Status: ${session.status}</div>
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
    if (currentSession) {
        info.innerHTML = `
            ${currentSession.assignment_name} - ${currentSession.course_name || `Course ${currentSession.course_id}`}
            <span style="margin-left: 20px;">Status: ${currentSession.status}</span>
        `;
    }
}

// Navigate to appropriate section based on status
function getNextSectionForStatus(status) {
    const sectionMap = {
        'preprocessing': 'upload-section',
        'name_matching_needed': 'matching-section',
        'ready': 'grading-section',
        'grading': 'grading-section',
        'complete': 'stats-section'
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
    const interval = setInterval(async () => {
        try {
            const response = await fetch(`${API_BASE}/sessions/${currentSession.id}`);
            const session = await response.json();

            if (session.status !== currentSession.status) {
                currentSession = session;
                updateSessionInfo();

                if (session.status === 'ready' || session.status === 'name_matching_needed') {
                    clearInterval(interval);
                    navigateToSection(getNextSectionForStatus(session.status));
                }
            }
        } catch (error) {
            console.error('Status check failed:', error);
        }
    }, 2000);
}
