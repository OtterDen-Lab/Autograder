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
                <strong>${session.assignment_name}</strong>
                <div>${session.course_name || `Course ${session.course_id}`}</div>
                <div>Status: ${session.status}</div>
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
    // New session button
    document.getElementById('new-session-btn').onclick = createNewSession;

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

// Create new session
async function createNewSession() {
    const courseId = prompt('Enter Course ID:');
    const assignmentId = prompt('Enter Assignment ID:');
    const assignmentName = prompt('Enter Assignment Name:');

    if (!courseId || !assignmentId || !assignmentName) return;

    try {
        const response = await fetch(`${API_BASE}/sessions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                course_id: parseInt(courseId),
                assignment_id: parseInt(assignmentId),
                assignment_name: assignmentName
            })
        });

        currentSession = await response.json();
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
