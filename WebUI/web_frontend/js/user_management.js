// User Management - Create and manage instructor/TA accounts

// Initialize user management when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    setupUserManagementListeners();
});

function setupUserManagementListeners() {
    const manageUsersBtn = document.getElementById('manage-users-btn');
    const userForm = document.getElementById('user-form');
    const cancelUserBtn = document.getElementById('cancel-user-btn');

    // Navigate to user management section
    if (manageUsersBtn) {
        manageUsersBtn.onclick = () => {
            navigateToSection('user-management-section');
            loadUsers();
        };
    }

    // Create user form submission
    if (userForm) {
        userForm.onsubmit = async (e) => {
            e.preventDefault();
            await createUser();
        };
    }

    // Cancel button returns to session selection
    if (cancelUserBtn) {
        cancelUserBtn.onclick = () => {
            resetUserForm();
            navigateToSection('session-section');
        };
    }
}

// Load and display all users
async function loadUsers() {
    try {
        const response = await fetch(`${API_BASE}/auth/users`, {
            credentials: 'include'
        });

        if (!response.ok) {
            throw new Error('Failed to load users');
        }

        const users = await response.json();
        displayUsers(users);
    } catch (error) {
        console.error('Error loading users:', error);
        document.getElementById('users-list').innerHTML = `
            <p style="color: var(--danger-color);">Error loading users: ${error.message}</p>
        `;
    }
}

// Display users in a table
function displayUsers(users) {
    const usersList = document.getElementById('users-list');

    if (users.length === 0) {
        usersList.innerHTML = '<p style="color: var(--gray-700);">No users found.</p>';
        return;
    }

    const tableHtml = `
        <table style="width: 100%; border-collapse: collapse;">
            <thead>
                <tr style="border-bottom: 2px solid var(--gray-200); text-align: left;">
                    <th style="padding: 10px;">Username</th>
                    <th style="padding: 10px;">Role</th>
                    <th style="padding: 10px;">Status</th>
                    <th style="padding: 10px;">Created</th>
                    <th style="padding: 10px;">Actions</th>
                </tr>
            </thead>
            <tbody>
                ${users.map(user => `
                    <tr style="border-bottom: 1px solid var(--gray-200);">
                        <td style="padding: 10px;"><strong>${user.username}</strong></td>
                        <td style="padding: 10px;">
                            <span style="
                                background: ${user.role === 'instructor' ? '#3b82f6' : '#10b981'};
                                color: white;
                                padding: 3px 10px;
                                border-radius: 12px;
                                font-size: 12px;
                                font-weight: 600;
                            ">
                                ${user.role === 'instructor' ? 'Instructor' : 'TA'}
                            </span>
                        </td>
                        <td style="padding: 10px;">
                            <span style="
                                background: ${user.is_active ? '#10b981' : '#6b7280'};
                                color: white;
                                padding: 3px 10px;
                                border-radius: 12px;
                                font-size: 12px;
                            ">
                                ${user.is_active ? 'Active' : 'Inactive'}
                            </span>
                        </td>
                        <td style="padding: 10px; color: var(--gray-700); font-size: 13px;">
                            ${new Date(user.created_at).toLocaleDateString()}
                        </td>
                        <td style="padding: 10px;">
                            ${user.is_active ? `
                                <button
                                    class="btn btn-small"
                                    onclick="deactivateUser(${user.id}, '${user.username}')"
                                    ${user.username === 'admin' ? 'disabled title="Cannot deactivate admin user"' : ''}
                                >
                                    Deactivate
                                </button>
                            ` : `
                                <span style="color: var(--gray-700); font-size: 13px;">Deactivated</span>
                            `}
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;

    usersList.innerHTML = tableHtml;
}

// Generate a random password
function generatePassword(length = 16) {
    const charset = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*';
    let password = '';
    const crypto = window.crypto || window.msCrypto;
    const randomValues = new Uint32Array(length);
    crypto.getRandomValues(randomValues);

    for (let i = 0; i < length; i++) {
        password += charset[randomValues[i] % charset.length];
    }
    return password;
}

// Create a new user
async function createUser() {
    const username = document.getElementById('user-username').value.trim();
    const role = document.getElementById('user-role').value;
    const errorDiv = document.getElementById('user-form-error');
    const successDiv = document.getElementById('user-created-success');
    const submitBtn = document.getElementById('create-user-submit-btn');

    // Validation
    if (!username) {
        showUserFormError('Please enter a username');
        return;
    }

    // Hide error and success
    errorDiv.style.display = 'none';
    successDiv.style.display = 'none';

    // Generate random password
    const password = generatePassword();

    // Disable submit button
    submitBtn.disabled = true;
    submitBtn.textContent = 'Creating...';

    try {
        const response = await fetch(`${API_BASE}/auth/users`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'include',
            body: JSON.stringify({
                username,
                email: null,  // Not using email
                full_name: null,
                role,
                password
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to create user');
        }

        const newUser = await response.json();

        // Show success with generated password
        document.getElementById('generated-password').textContent = password;
        successDiv.style.display = 'block';

        // Setup copy button
        document.getElementById('copy-password-btn').onclick = () => {
            navigator.clipboard.writeText(password).then(() => {
                const btn = document.getElementById('copy-password-btn');
                const originalText = btn.textContent;
                btn.textContent = '✓ Copied!';
                setTimeout(() => {
                    btn.textContent = originalText;
                }, 2000);
            });
        };

        // Clear form fields but keep the success message visible
        document.getElementById('user-username').value = '';
        document.getElementById('user-role').value = 'ta';

        // Reload users list
        loadUsers();

        // Re-enable submit button
        submitBtn.disabled = false;
        submitBtn.textContent = 'Create User';
    } catch (error) {
        console.error('Error creating user:', error);
        showUserFormError(error.message);

        // Re-enable submit button
        submitBtn.disabled = false;
        submitBtn.textContent = 'Create User';
    }
}

// Deactivate a user (soft delete)
async function deactivateUser(userId, username) {
    if (!confirm(`Are you sure you want to deactivate user "${username}"? They will no longer be able to log in.`)) {
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/auth/users/${userId}`, {
            method: 'DELETE',
            credentials: 'include'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to deactivate user');
        }

        // Reload users list
        loadUsers();
        alert(`User "${username}" has been deactivated.`);
    } catch (error) {
        console.error('Error deactivating user:', error);
        alert(`Error: ${error.message}`);
    }
}

// Show error message in user form
function showUserFormError(message) {
    const errorDiv = document.getElementById('user-form-error');
    errorDiv.textContent = message;
    errorDiv.style.display = 'block';
}

// Reset user form to initial state
function resetUserForm() {
    document.getElementById('user-form').reset();
    document.getElementById('user-form-error').style.display = 'none';
}
