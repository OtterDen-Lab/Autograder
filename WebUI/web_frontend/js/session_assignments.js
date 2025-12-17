// Session Assignments - Assign TAs to grading sessions

// Initialize session assignment listeners
document.addEventListener('DOMContentLoaded', () => {
    setupSessionAssignmentListeners();
});

function setupSessionAssignmentListeners() {
    const assignTaBtn = document.getElementById('assign-ta-btn');

    if (assignTaBtn) {
        assignTaBtn.onclick = async () => {
            await assignTAToSession();
        };
    }
}

// Load and display TA assignments for the current session
async function loadSessionAssignments() {
    if (!currentSession || !currentUser || currentUser.role !== 'instructor') {
        return; // Only instructors can see assignments
    }

    const container = document.getElementById('ta-assignment-container');
    if (!container) return;

    try {
        // Load all TAs (for the dropdown)
        const usersResponse = await fetch(`${API_BASE}/auth/users`, {
            credentials: 'include'
        });

        if (!usersResponse.ok) {
            throw new Error('Failed to load users');
        }

        const allUsers = await usersResponse.json();
        const tas = allUsers.filter(u => u.role === 'ta' && u.is_active);

        // Load assigned TAs for this session
        const assignmentsResponse = await fetch(
            `${API_BASE}/sessions/${currentSession.id}/assignments`,
            { credentials: 'include' }
        );

        if (!assignmentsResponse.ok) {
            throw new Error('Failed to load assignments');
        }

        const assignments = await assignmentsResponse.json();

        // Display assigned TAs
        displayAssignedTAs(assignments);

        // Populate TA dropdown (excluding already assigned TAs)
        populateTADropdown(tas, assignments);

        // Show the container
        container.style.display = 'block';
    } catch (error) {
        console.error('Error loading session assignments:', error);
        container.style.display = 'none';
    }
}

// Display list of assigned TAs
function displayAssignedTAs(assignments) {
    const listDiv = document.getElementById('assigned-tas-list');

    if (assignments.length === 0) {
        listDiv.innerHTML = `
            <p style="color: var(--gray-700); font-style: italic;">
                No TAs assigned yet. Assign TAs below to give them access to this session.
            </p>
        `;
        return;
    }

    const taCards = assignments.map(assignment => `
        <div style="
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 15px;
            background: var(--gray-100);
            border-radius: 6px;
            margin-bottom: 8px;
        ">
            <div>
                <strong>${assignment.username}</strong>
                ${assignment.full_name ? `<span style="color: var(--gray-700);"> - ${assignment.full_name}</span>` : ''}
                <br>
                <small style="color: var(--gray-700);">
                    Assigned ${new Date(assignment.assigned_at).toLocaleDateString()}
                </small>
            </div>
            <button
                class="btn btn-small"
                onclick="unassignTA(${assignment.user_id}, '${assignment.username}')"
            >
                Remove
            </button>
        </div>
    `).join('');

    listDiv.innerHTML = taCards;
}

// Populate dropdown with available TAs (not yet assigned)
function populateTADropdown(allTAs, assignments) {
    const select = document.getElementById('ta-select');
    const assignedUserIds = new Set(assignments.map(a => a.user_id));

    // Filter out already assigned TAs
    const availableTAs = allTAs.filter(ta => !assignedUserIds.has(ta.id));

    if (availableTAs.length === 0) {
        select.innerHTML = '<option value="">No TAs available to assign</option>';
        select.disabled = true;
        document.getElementById('assign-ta-btn').disabled = true;
        return;
    }

    select.innerHTML = `
        <option value="">Select a TA to assign...</option>
        ${availableTAs.map(ta => `
            <option value="${ta.id}">
                ${ta.username}${ta.full_name ? ` - ${ta.full_name}` : ''}
            </option>
        `).join('')}
    `;
    select.disabled = false;
    document.getElementById('assign-ta-btn').disabled = false;
}

// Assign a TA to the current session
async function assignTAToSession() {
    const select = document.getElementById('ta-select');
    const userId = parseInt(select.value);

    if (!userId) {
        alert('Please select a TA to assign');
        return;
    }

    if (!currentSession) {
        alert('No session selected');
        return;
    }

    try {
        const response = await fetch(
            `${API_BASE}/sessions/${currentSession.id}/assign`,
            {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                credentials: 'include',
                body: JSON.stringify({ user_id: userId })
            }
        );

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to assign TA');
        }

        // Reload assignments
        await loadSessionAssignments();

        alert('TA assigned successfully!');
    } catch (error) {
        console.error('Error assigning TA:', error);
        alert(`Error: ${error.message}`);
    }
}

// Unassign a TA from the current session
async function unassignTA(userId, username) {
    if (!confirm(`Remove ${username} from this grading session? They will lose access to grade this session.`)) {
        return;
    }

    if (!currentSession) {
        alert('No session selected');
        return;
    }

    try {
        const response = await fetch(
            `${API_BASE}/sessions/${currentSession.id}/assign/${userId}`,
            {
                method: 'DELETE',
                credentials: 'include'
            }
        );

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to unassign TA');
        }

        // Reload assignments
        await loadSessionAssignments();

        alert(`${username} has been removed from this session.`);
    } catch (error) {
        console.error('Error unassigning TA:', error);
        alert(`Error: ${error.message}`);
    }
}
