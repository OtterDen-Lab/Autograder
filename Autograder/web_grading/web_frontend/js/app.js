// Main application logic

const API_BASE = '/api';
let currentSession = null;

// Helper function to recursively get all files from dropped items (including directories)
async function getAllFilesFromDataTransfer(dataTransferItems) {
    const files = [];

    // Process each dropped item
    for (const item of dataTransferItems) {
        const entry = item.webkitGetAsEntry ? item.webkitGetAsEntry() : null;

        if (entry) {
            if (entry.isFile) {
                const file = await new Promise((resolve) => entry.file(resolve));
                files.push(file);
            } else if (entry.isDirectory) {
                const dirFiles = await readDirectory(entry);
                files.push(...dirFiles);
            }
        } else {
            // Fallback for browsers that don't support webkitGetAsEntry
            const file = item.getAsFile();
            if (file) files.push(file);
        }
    }

    return files;
}

// Recursively read all files in a directory
async function readDirectory(directoryEntry) {
    const files = [];
    const reader = directoryEntry.createReader();

    // readEntries must be called repeatedly until it returns empty array
    const readEntries = async () => {
        return new Promise((resolve, reject) => {
            reader.readEntries(resolve, reject);
        });
    };

    let entries;
    do {
        entries = await readEntries();
        for (const entry of entries) {
            if (entry.isFile) {
                const file = await new Promise((resolve) => entry.file(resolve));
                files.push(file);
            } else if (entry.isDirectory) {
                const subFiles = await readDirectory(entry);
                files.push(...subFiles);
            }
        }
    } while (entries.length > 0);

    return files;
}

// Initialize app
document.addEventListener('DOMContentLoaded', () => {
    loadSessions();
    setupEventListeners();
    initializeAIProvider();
});

// Initialize AI provider dropdown from localStorage
function initializeAIProvider() {
    const aiProviderSelect = document.getElementById('global-ai-provider-select');

    // Load saved preference from localStorage (default to 'anthropic')
    const savedProvider = localStorage.getItem('ai_provider') || 'anthropic';
    aiProviderSelect.value = savedProvider;

    // Save to localStorage whenever user changes selection
    aiProviderSelect.addEventListener('change', (e) => {
        localStorage.setItem('ai_provider', e.target.value);
        console.log(`AI provider changed to: ${e.target.value}`);
    });
}

// Get current AI provider selection
function getAIProvider() {
    return localStorage.getItem('ai_provider') || 'anthropic';
}

// Get status badge HTML with color coding
function getStatusBadge(status) {
    const statusConfig = {
        'preprocessing': { label: 'Processing', color: '#3b82f6' },  // blue
        'awaiting_alignment': { label: 'Awaiting Alignment', color: '#f97316' },  // orange
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
        'awaiting_alignment': 'upload-section',
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

    uploadArea.ondrop = async (e) => {
        e.preventDefault();
        uploadArea.style.borderColor = 'var(--gray-200)';

        // Handle both files and directories
        const items = e.dataTransfer.items;
        console.log('Drop event - number of items:', items ? items.length : 0);

        if (items) {
            console.log('Processing items...');
            const files = await getAllFilesFromDataTransfer(items);
            console.log('Total files extracted from drop:', files.length);
            console.log('File names:', files.map(f => f.name));

            // Filter to only PDF files
            const pdfFiles = files.filter(file =>
                file.name.toLowerCase().endsWith('.pdf') &&
                !file.name.startsWith('.')
            );
            console.log('PDF files after filtering:', pdfFiles.length);

            if (pdfFiles.length > 0) {
                // Create a new FileList-like object
                const dataTransfer = new DataTransfer();
                pdfFiles.forEach(file => dataTransfer.items.add(file));
                fileInput.files = dataTransfer.files;
                console.log('About to upload', fileInput.files.length, 'files');
                uploadFiles();
            } else {
                alert('No PDF files found. Please upload PDF files only.');
            }
        } else {
            // Fallback for older browsers - still filter PDFs
            const pdfFiles = Array.from(e.dataTransfer.files).filter(file =>
                file.name.toLowerCase().endsWith('.pdf') &&
                !file.name.startsWith('.')
            );
            console.log('Fallback mode - PDF files:', pdfFiles.length);
            if (pdfFiles.length > 0) {
                const dataTransfer = new DataTransfer();
                pdfFiles.forEach(file => dataTransfer.items.add(file));
                fileInput.files = dataTransfer.files;
                uploadFiles();
            } else {
                alert('No PDF files found. Please upload PDF files only.');
            }
        }
    };

    fileInput.onchange = uploadFiles;

    // Upload more exams button from grading section
    document.getElementById('upload-more-btn').onclick = () => {
        if (!currentSession) return;

        // Reset upload area
        document.getElementById('upload-area').style.display = 'block';
        document.getElementById('upload-progress-container').style.display = 'none';
        document.getElementById('file-input').value = '';

        // Show helpful message
        const messageDiv = document.getElementById('initial-upload-message');
        messageDiv.style.display = 'block';
        messageDiv.innerHTML = '<strong>Adding more exams:</strong> Your existing split points and settings will be reused automatically.';

        navigateToSection('upload-section');
    };

    // Upload more exams button from stats section
    document.getElementById('upload-more-stats-btn').onclick = () => {
        if (!currentSession) return;

        // Reset upload area
        document.getElementById('upload-area').style.display = 'block';
        document.getElementById('upload-progress-container').style.display = 'none';
        document.getElementById('file-input').value = '';

        // Show helpful message
        const messageDiv = document.getElementById('initial-upload-message');
        messageDiv.style.display = 'block';
        messageDiv.innerHTML = '<strong>Adding more exams:</strong> Your existing split points and settings will be reused automatically.';

        navigateToSection('upload-section');
    };

    // Re-run blank detection button
    document.getElementById('rerun-blank-detection-btn').onclick = async () => {
        if (!currentSession) return;

        if (!confirm('Re-run blank detection on all problems in this session? This will update the blank detection results for all problems.')) {
            return;
        }

        const btn = document.getElementById('rerun-blank-detection-btn');
        const originalText = btn.textContent;
        btn.disabled = true;
        btn.textContent = '⏳ Starting...';

        try {
            const eventSource = new EventSource(`${API_BASE}/sessions/${currentSession.id}/rerun-blank-detection`);

            let totalProblems = 0;
            let currentProblem = 0;
            let blankCount = 0;
            let notBlankCount = 0;
            let errorCount = 0;

            eventSource.onmessage = (event) => {
                const data = JSON.parse(event.data);

                if (data.error) {
                    alert(`Error: ${data.error}`);
                    eventSource.close();
                    btn.disabled = false;
                    btn.textContent = originalText;
                    return;
                }

                switch (data.type) {
                    case 'start':
                        totalProblems = data.total;
                        btn.textContent = `⏳ Processing 0/${totalProblems}...`;
                        break;

                    case 'progress':
                        currentProblem = data.current;
                        if (data.is_blank) blankCount++;
                        else notBlankCount++;
                        btn.textContent = `⏳ Processing ${currentProblem}/${totalProblems} (${blankCount} blank, ${notBlankCount} not blank)`;
                        break;

                    case 'error':
                        errorCount++;
                        console.error(`Error on problem ${data.problem_number}: ${data.message}`);
                        break;

                    case 'complete':
                        eventSource.close();
                        btn.disabled = false;
                        btn.textContent = originalText;

                        alert(`Blank detection re-run complete!\n\n` +
                              `Total problems: ${data.total_problems}\n` +
                              `Blank detected: ${data.blank_detected}\n` +
                              `Not blank: ${data.not_blank}\n` +
                              `Errors: ${data.errors}`);

                        // Refresh stats display
                        showStatistics();
                        break;
                }
            };

            eventSource.onerror = (error) => {
                console.error('EventSource error:', error);
                eventSource.close();
                btn.disabled = false;
                btn.textContent = originalText;
                alert('Connection error during blank detection. Check console for details.');
            };

        } catch (error) {
            console.error('Error re-running blank detection:', error);
            alert('Failed to re-run blank detection. Check console for details.');
            btn.disabled = false;
            btn.textContent = originalText;
        }
    };
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
        // Upload the files
        const response = await fetch(`${API_BASE}/uploads/${currentSession.id}/upload`, {
            method: 'POST',
            body: formData
        });

        const result = await response.json();
        console.log('Upload response:', result);

        // Check if we need to show alignment interface
        // Only show if awaiting alignment AND not auto-processed
        if (result.status === 'awaiting_alignment' && result.composites && !result.auto_processed) {
            console.log('Showing alignment interface with', Object.keys(result.composites).length, 'pages');
            showAlignmentInterface(result.composites, result.page_dimensions, result.num_exams);
        } else {
            console.log('Not showing alignment interface. Status:', result.status, 'Has composites:', !!result.composites, 'Auto-processed:', result.auto_processed);
            // Connect to SSE stream for processing updates
            listenForStatusUpdates();
            document.getElementById('upload-status').textContent = result.message;
        }

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

// Show alignment interface for manual split point selection
let splitPoints = {};
let compositeData = null;

function showAlignmentInterface(composites, pageDimensions, numExams) {
    compositeData = {
        composites: composites,
        page_dimensions: pageDimensions,
        num_exams: numExams
    };
    splitPoints = {};

    // Hide upload area, show alignment interface
    document.getElementById('upload-area').style.display = 'none';

    const container = document.getElementById('upload-progress-container');
    container.style.display = 'block';

    const statusDiv = document.getElementById('upload-status');
    statusDiv.innerHTML = `
        <h3>Manual Exam Alignment</h3>
        <p>Click on the composite images below to mark where questions should be split.</p>
        <p><strong>Instructions:</strong></p>
        <ul style="text-align: left; margin: 10px 0;">
            <li><strong>Click</strong> on the image to add a split line</li>
            <li><strong>Click</strong> a red line to remove it</li>
            <li>Split lines mark the <strong>top</strong> of each question</li>
        </ul>
        <div style="background: #eff6ff; padding: 10px; border-radius: 6px; margin: 10px 0;">
            <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                <input type="checkbox" id="skip-first-region-checkbox" checked>
                <span style="color: var(--gray-700);">Skip first region (header/name area)</span>
            </label>
            <small style="display: block; margin-top: 5px; color: var(--gray-600);">
                ℹ️ The first split line usually marks the header/title area, not a question. Leave this checked to skip it.
            </small>
        </div>
        <div style="background: #eff6ff; padding: 10px; border-radius: 6px; margin: 10px 0;">
            <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                <input type="checkbox" id="last-page-blank-checkbox">
                <span style="color: var(--gray-700);">Last page is blank</span>
            </label>
            <small style="display: block; margin-top: 5px; color: var(--gray-600);">
                ℹ️ Check this if the last page is blank (common with odd-numbered page counts). The last page will be skipped during processing.
            </small>
        </div>
        <div style="background: #fef3c7; padding: 10px; border-radius: 6px; margin: 10px 0;">
            <strong style="color: var(--gray-800);">📄 Multi-page questions:</strong>
            <p style="color: var(--gray-700); margin: 5px 0; font-size: 14px;">
                Questions can span multiple pages. Use the checkboxes between pages to explicitly break questions at page boundaries when needed.
            </p>
            <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; margin-top: 10px;">
                <input type="checkbox" id="break-all-boundaries-checkbox">
                <span style="color: var(--gray-800); font-weight: 500;">Break all page boundaries (questions don't span pages)</span>
            </label>
        </div>
        <div style="display: flex; gap: 10px; margin-top: 15px;">
            <button id="submit-alignment-btn" class="btn btn-primary">Submit Split Points & Process Exams</button>
            <button id="cancel-alignment-btn" class="btn btn-secondary">Cancel</button>
        </div>
    `;

    // Clear and populate progress area with composite images
    const progressFill = document.getElementById('upload-progress-fill');
    progressFill.innerHTML = '';

    const pagesContainer = document.createElement('div');
    pagesContainer.id = 'alignment-pages-container';
    pagesContainer.style.marginTop = '20px';

    const pageNumbers = Object.keys(composites).map(n => parseInt(n)).sort((a, b) => a - b);

    for (let i = 0; i < pageNumbers.length; i++) {
        const pageNum = pageNumbers[i];
        const imageBase64 = composites[pageNum];

        const pageSection = createAlignmentPageSection(pageNum, imageBase64);
        pagesContainer.appendChild(pageSection);

        // Add page boundary control between pages (not after last page)
        if (i < pageNumbers.length - 1) {
            const nextPageNum = pageNumbers[i + 1];
            const boundaryControl = createPageBoundaryControl(pageNum, nextPageNum);
            pagesContainer.appendChild(boundaryControl);
        }
    }

    container.appendChild(pagesContainer);

    // Add bottom controls with second "last page blank" checkbox
    const bottomControls = document.createElement('div');
    bottomControls.style.cssText = 'margin: 20px; text-align: center;';
    bottomControls.innerHTML = `
        <div style="background: #eff6ff; padding: 10px; border-radius: 6px; margin: 10px auto; max-width: 600px;">
            <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                <input type="checkbox" id="last-page-blank-checkbox-bottom">
                <span style="color: var(--gray-700);">Last page is blank</span>
            </label>
            <small style="display: block; margin-top: 5px; color: var(--gray-600);">
                ℹ️ Check this if the last page is blank (common with odd-numbered page counts). The last page will be skipped during processing.
            </small>
        </div>
        <div style="display: flex; gap: 10px; justify-content: center; margin-top: 15px;">
            <button id="submit-alignment-bottom-btn" class="btn btn-primary">Submit Split Points & Process Exams</button>
            <button id="go-to-top-btn" class="btn btn-secondary">↑ Go to Top</button>
        </div>
    `;
    container.appendChild(bottomControls);

    // Setup button handlers
    document.getElementById('submit-alignment-btn').onclick = submitAlignment;
    document.getElementById('submit-alignment-bottom-btn').onclick = submitAlignment;
    document.getElementById('cancel-alignment-btn').onclick = cancelAlignment;
    document.getElementById('go-to-top-btn').onclick = () => {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    };

    // Sync the two "last page blank" checkboxes
    const topCheckbox = document.getElementById('last-page-blank-checkbox');
    const bottomCheckbox = document.getElementById('last-page-blank-checkbox-bottom');

    topCheckbox.addEventListener('change', (e) => {
        bottomCheckbox.checked = e.target.checked;
    });

    bottomCheckbox.addEventListener('change', (e) => {
        topCheckbox.checked = e.target.checked;
    });

    // Handle "break all boundaries" checkbox
    const breakAllCheckbox = document.getElementById('break-all-boundaries-checkbox');
    breakAllCheckbox.addEventListener('change', (e) => {
        const shouldBreak = e.target.checked;

        // Loop through all page boundary checkboxes and set their state
        for (let i = 0; i < pageNumbers.length - 1; i++) {
            const currentPageNum = pageNumbers[i];
            const nextPageNum = pageNumbers[i + 1];
            const boundaryCheckbox = document.getElementById(`page-boundary-${currentPageNum}-${nextPageNum}`);

            if (boundaryCheckbox && boundaryCheckbox.checked !== shouldBreak) {
                boundaryCheckbox.checked = shouldBreak;
                // Trigger the change event to update the visual lines
                boundaryCheckbox.dispatchEvent(new Event('change'));
            }
        }
    });
}

function createPageBoundaryControl(currentPageNum, nextPageNum) {
    const control = document.createElement('div');
    control.style.cssText = 'background: #f3f4f6; border: 2px dashed var(--gray-300); padding: 15px; margin: 20px 0; text-align: center; border-radius: 8px;';

    const label = document.createElement('label');
    label.style.cssText = 'display: inline-flex; align-items: center; gap: 10px; cursor: pointer; font-weight: 500; color: var(--gray-800);';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `page-boundary-${currentPageNum}-${nextPageNum}`;
    checkbox.style.cssText = 'width: 18px; height: 18px; cursor: pointer;';

    const labelText = document.createElement('span');
    labelText.textContent = `Break question at page boundary (between Page ${currentPageNum + 1} and Page ${nextPageNum + 1})`;

    // Handle checkbox change
    checkbox.addEventListener('change', (e) => {
        if (e.target.checked) {
            // Add split at end of current page (which is same as start of next page)
            const pageDims = compositeData.page_dimensions[currentPageNum];
            const pageHeight = pageDims.height;

            // Add to current page at the very bottom
            if (!splitPoints[currentPageNum]) {
                splitPoints[currentPageNum] = [];
            }

            // Check if split already exists at page boundary
            if (!splitPoints[currentPageNum].includes(pageHeight)) {
                splitPoints[currentPageNum].push(pageHeight);
                splitPoints[currentPageNum].sort((a, b) => a - b);

                // Update visual lines for current page (bottom)
                const canvas = document.getElementById(`alignment-canvas-${currentPageNum}`);
                const canvasContainer = canvas.parentElement;
                updateSplitLines(currentPageNum, canvasContainer, canvas);

                // Also add a cosmetic split at the top of the next page (y=0)
                if (!splitPoints[nextPageNum]) {
                    splitPoints[nextPageNum] = [];
                }
                if (!splitPoints[nextPageNum].includes(0)) {
                    splitPoints[nextPageNum].push(0);
                    splitPoints[nextPageNum].sort((a, b) => a - b);

                    // Update visual lines for next page (top)
                    const nextCanvas = document.getElementById(`alignment-canvas-${nextPageNum}`);
                    const nextCanvasContainer = nextCanvas.parentElement;
                    updateSplitLines(nextPageNum, nextCanvasContainer, nextCanvas);
                }
            }
        } else {
            // Remove split at page boundary
            const pageDims = compositeData.page_dimensions[currentPageNum];
            const pageHeight = pageDims.height;

            if (splitPoints[currentPageNum]) {
                splitPoints[currentPageNum] = splitPoints[currentPageNum].filter(y => y !== pageHeight);
                if (splitPoints[currentPageNum].length === 0) {
                    delete splitPoints[currentPageNum];
                }

                // Update visual lines for current page
                const canvas = document.getElementById(`alignment-canvas-${currentPageNum}`);
                const canvasContainer = canvas.parentElement;
                updateSplitLines(currentPageNum, canvasContainer, canvas);
            }

            // Also remove the split at the top of next page (y=0)
            if (splitPoints[nextPageNum]) {
                splitPoints[nextPageNum] = splitPoints[nextPageNum].filter(y => y !== 0);
                if (splitPoints[nextPageNum].length === 0) {
                    delete splitPoints[nextPageNum];
                }

                // Update visual lines for next page
                const nextCanvas = document.getElementById(`alignment-canvas-${nextPageNum}`);
                const nextCanvasContainer = nextCanvas.parentElement;
                updateSplitLines(nextPageNum, nextCanvasContainer, nextCanvas);
            }
        }
    });

    label.appendChild(checkbox);
    label.appendChild(labelText);
    control.appendChild(label);

    return control;
}

function createAlignmentPageSection(pageNum, imageBase64) {
    const section = document.createElement('div');
    section.className = 'alignment-page-section';
    section.style.cssText = 'margin-bottom: 20px; border: 1px solid var(--gray-200); border-radius: 8px; overflow: hidden;';

    const header = document.createElement('div');
    header.style.cssText = 'background: var(--primary-color); color: white; padding: 15px; font-weight: bold;';
    header.textContent = `Page ${pageNum + 1}`;

    const canvasContainer = document.createElement('div');
    canvasContainer.style.cssText = 'position: relative; margin: 20px; cursor: crosshair;';

    const canvas = document.createElement('canvas');
    canvas.id = `alignment-canvas-${pageNum}`;
    canvas.style.cssText = 'border: 1px solid var(--gray-200); max-width: 100%; height: auto;';

    const img = new Image();
    img.onload = () => {
        canvas.width = img.width;
        canvas.height = img.height;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0);
    };
    img.src = `data:image/png;base64,${imageBase64}`;

    // Click to add split line
    canvas.addEventListener('click', (e) => {
        const rect = canvas.getBoundingClientRect();
        // Get click position relative to the displayed canvas
        const clickY = e.clientY - rect.top;

        // Convert from displayed canvas coordinates to actual canvas pixel coordinates
        const canvasY = (clickY / rect.height) * canvas.height;

        // Convert from canvas pixel coordinates to PDF coordinates
        const pageDims = compositeData.page_dimensions[pageNum];
        const pdfY = Math.round((canvasY / canvas.height) * pageDims.height);

        console.log(`Click: displayY=${clickY.toFixed(1)}, canvasY=${canvasY.toFixed(1)}, pdfY=${pdfY}, canvas.height=${canvas.height}, rect.height=${rect.height.toFixed(1)}`);

        addSplitLine(pageNum, pdfY, canvasContainer, canvas);
    });

    canvasContainer.appendChild(canvas);

    const helpText = document.createElement('p');
    helpText.style.cssText = 'margin: 10px 20px; color: var(--gray-700); font-size: 14px;';
    helpText.innerHTML = `
        <strong>Click</strong> on the composite image to add split points.<br>
        <strong>Click</strong> a red line to remove it.
    `;

    section.appendChild(header);
    section.appendChild(helpText);
    section.appendChild(canvasContainer);

    return section;
}

function addSplitLine(pageNum, pdfY, container, canvas) {
    if (!splitPoints[pageNum]) {
        splitPoints[pageNum] = [];
    }

    splitPoints[pageNum].push(pdfY);
    splitPoints[pageNum].sort((a, b) => a - b);

    updateSplitLines(pageNum, container, canvas);
}

function updateSplitLines(pageNum, container, canvas) {
    // Remove existing lines
    container.querySelectorAll('.alignment-split-line').forEach(el => el.remove());

    const pageDims = compositeData.page_dimensions[pageNum];
    const rect = canvas.getBoundingClientRect();

    (splitPoints[pageNum] || []).forEach((pdfY, idx) => {
        // Convert from PDF coordinates to canvas pixel coordinates
        const canvasY = (pdfY / pageDims.height) * canvas.height;

        // Convert from canvas pixel coordinates to displayed canvas coordinates
        const displayY = (canvasY / canvas.height) * rect.height;

        const line = document.createElement('div');
        line.className = 'alignment-split-line';
        line.style.cssText = `
            position: absolute;
            left: 0;
            right: 0;
            height: 3px;
            background: red;
            cursor: pointer;
            opacity: 0.7;
            top: ${displayY}px;
        `;

        const label = document.createElement('div');
        label.style.cssText = `
            position: absolute;
            right: 5px;
            top: -20px;
            background: red;
            color: white;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 12px;
            pointer-events: none;
        `;
        label.textContent = `Split ${idx + 1}`;

        // Click to remove
        line.addEventListener('click', (e) => {
            e.stopPropagation();
            splitPoints[pageNum] = splitPoints[pageNum].filter(y => y !== pdfY);
            if (splitPoints[pageNum].length === 0) {
                delete splitPoints[pageNum];
            }
            updateSplitLines(pageNum, container, canvas);
        });

        line.addEventListener('mouseenter', () => {
            line.style.opacity = '1';
            line.style.height = '5px';
        });

        line.addEventListener('mouseleave', () => {
            line.style.opacity = '0.7';
            line.style.height = '3px';
        });

        line.appendChild(label);
        container.appendChild(line);
    });
}

// Calculate expected problem count from split points
function calculateProblemCount(splitPoints, skipFirstRegion, lastPageBlank) {
    // The backend creates a linear list of (page, y) splits, then:
    // 1. Adds (0, 0) at start if not present
    // 2. Adds (last_page, page_height) at end if not present
    // 3. Each consecutive pair of splits defines one region
    // 4. If skip_first_region=True, skip the first pair

    // Count total split points provided by user
    let totalSplits = 0;
    for (const pageNum in splitPoints) {
        totalSplits += splitPoints[pageNum].length;
    }

    console.log('Problem count calculation:');
    console.log('  User splits:', totalSplits);
    console.log('  Split points by page:', splitPoints);
    console.log('  Skip first region:', skipFirstRegion);

    // Backend always adds start (0,0) and end splits, so:
    // linear_splits.length = user_splits + 2 (start and end)
    // regions = linear_splits.length - 1
    // problems = regions - (1 if skip_first_region else 0)

    let linearSplitsCount = totalSplits + 2; // Add implicit start and end
    let regions = linearSplitsCount - 1;

    console.log('  Linear splits count:', linearSplitsCount);
    console.log('  Regions:', regions);

    // Subtract first region if skipping header
    let problems = regions;
    if (skipFirstRegion) {
        problems -= 1;
    }

    console.log('  Final problems:', problems);

    // Note: lastPageBlank is handled in backend after problem extraction
    // so we don't adjust the count here

    return Math.max(1, problems); // At least 1 problem
}

async function submitAlignment() {
    try {
        // Check if we should skip the first region
        const skipFirstRegion = document.getElementById('skip-first-region-checkbox').checked;

        // Check if last page is blank
        const lastPageBlank = document.getElementById('last-page-blank-checkbox').checked;

        // Calculate expected problem count
        const problemCount = calculateProblemCount(splitPoints, skipFirstRegion, lastPageBlank);

        // Show confirmation dialog
        const confirmed = confirm(
            `Based on your split points, this will create ${problemCount} problem(s) per exam.\n\n` +
            `Is this correct?\n\n` +
            `(Click OK to proceed with processing, or Cancel to adjust your split points)`
        );

        if (!confirmed) {
            return; // User cancelled
        }

        document.getElementById('submit-alignment-btn').disabled = true;
        document.getElementById('submit-alignment-btn').textContent = 'Submitting...';
        document.getElementById('submit-alignment-bottom-btn').disabled = true;
        document.getElementById('submit-alignment-bottom-btn').textContent = 'Submitting...';

        // Convert all split points to integers (they may be floats from page dimensions)
        const splitPointsInt = {};
        for (const [pageNum, points] of Object.entries(splitPoints)) {
            splitPointsInt[pageNum] = points.map(y => Math.round(y));
        }

        // Get AI provider from localStorage
        const aiProvider = getAIProvider();

        // Submit split points to backend
        const response = await fetch(`${API_BASE}/uploads/${currentSession.id}/submit-alignment`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                split_points: splitPointsInt,
                skip_first_region: skipFirstRegion,
                last_page_blank: lastPageBlank,
                ai_provider: aiProvider
            })
        });

        if (!response.ok) {
            throw new Error('Failed to submit alignment');
        }

        // Clean up alignment interface
        document.getElementById('alignment-pages-container').remove();
        document.getElementById('upload-status').textContent = 'Processing exams with manual split points...';

        // Start listening for processing updates
        listenForStatusUpdates();

    } catch (error) {
        console.error('Failed to submit alignment:', error);
        alert('Failed to submit alignment: ' + error.message);
        document.getElementById('submit-alignment-btn').disabled = false;
        document.getElementById('submit-alignment-btn').textContent = 'Submit Split Points & Process Exams';
        document.getElementById('submit-alignment-bottom-btn').disabled = false;
        document.getElementById('submit-alignment-bottom-btn').textContent = 'Submit Split Points & Process Exams';
    }
}

function cancelAlignment() {
    if (confirm('Cancel alignment? You will need to re-upload the exams.')) {
        // Reset the upload section
        document.getElementById('upload-area').style.display = 'block';
        document.getElementById('upload-progress-container').style.display = 'none';
        document.getElementById('file-input').value = '';
        splitPoints = {};
        compositeData = null;
    }
}
