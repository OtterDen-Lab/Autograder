// Login page logic

const API_BASE = '/api';

document.addEventListener('DOMContentLoaded', () => {
    const loginForm = document.getElementById('login-form');
    const usernameInput = document.getElementById('username');
    const passwordInput = document.getElementById('password');
    const errorMessage = document.getElementById('error-message');
    const loginButton = document.getElementById('login-button');

    loginForm.addEventListener('submit', async (e) => {
        e.preventDefault();

        const username = usernameInput.value.trim();
        const password = passwordInput.value;

        // Validate inputs
        if (!username || !password) {
            showError('Please enter both username and password');
            return;
        }

        // Disable form while logging in
        loginButton.disabled = true;
        loginButton.textContent = 'Signing in...';
        errorMessage.style.display = 'none';

        try {
            const response = await fetch(`${API_BASE}/auth/login`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ username, password }),
                credentials: 'include'  // Important: include cookies
            });

            if (response.ok) {
                // Login successful - redirect to main app
                window.location.href = '/';
            } else {
                // Login failed - show error
                const data = await response.json();
                showError(data.detail || 'Invalid username or password');

                // Re-enable form
                loginButton.disabled = false;
                loginButton.textContent = 'Sign In';
                passwordInput.value = '';
                passwordInput.focus();
            }
        } catch (error) {
            console.error('Login error:', error);
            showError('Connection error. Please try again.');

            // Re-enable form
            loginButton.disabled = false;
            loginButton.textContent = 'Sign In';
        }
    });

    function showError(message) {
        errorMessage.textContent = message;
        errorMessage.style.display = 'block';
    }
});
