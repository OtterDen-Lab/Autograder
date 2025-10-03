// Grading interface logic

let currentProblem = null;
let currentProblemNumber = 1;

// Initialize grading interface when section becomes active
function initializeGrading() {
    if (!currentSession) return;

    loadProblemNumbers();
    setupGradingControls();
}

// Load available problem numbers
async function loadProblemNumbers() {
    // TODO: Get problem numbers from session
    // For now, hardcode 1-10
    const select = document.getElementById('problem-select');
    select.innerHTML = '';

    for (let i = 1; i <= 10; i++) {
        const option = document.createElement('option');
        option.value = i;
        option.textContent = `Problem ${i}`;
        select.appendChild(option);
    }

    select.value = currentProblemNumber;
    select.onchange = () => {
        currentProblemNumber = parseInt(select.value);
        loadNextProblem();
    };

    loadNextProblem();
}

// Setup grading controls
function setupGradingControls() {
    document.getElementById('submit-grade-btn').onclick = submitGrade;
    document.getElementById('next-problem-btn').onclick = loadNextProblem;
}

// Load next ungraded problem
async function loadNextProblem() {
    try {
        const response = await fetch(
            `${API_BASE}/problems/${currentSession.id}/${currentProblemNumber}/next`
        );

        if (response.status === 404) {
            // No more problems for this number
            alert(`All problems for Problem ${currentProblemNumber} are graded!`);

            // Try to advance to next problem number
            if (currentProblemNumber < 10) {
                currentProblemNumber++;
                document.getElementById('problem-select').value = currentProblemNumber;
                loadNextProblem();
            } else {
                // All done!
                navigateToSection('stats-section');
                loadStatistics();
            }
            return;
        }

        currentProblem = await response.json();

        // Display problem
        document.getElementById('problem-image').src =
            `data:image/png;base64,${currentProblem.image_data}`;

        // Update progress
        document.getElementById('grading-progress').textContent =
            `${currentProblem.current_index} / ${currentProblem.total_count}`;

        // Clear form
        document.getElementById('score-input').value = '';
        document.getElementById('feedback-input').value = '';

    } catch (error) {
        console.error('Failed to load problem:', error);
        alert('Failed to load problem');
    }
}

// Submit grade for current problem
async function submitGrade() {
    if (!currentProblem) return;

    const score = parseFloat(document.getElementById('score-input').value);
    const feedback = document.getElementById('feedback-input').value;

    if (isNaN(score)) {
        alert('Please enter a valid score');
        return;
    }

    try {
        await fetch(`${API_BASE}/problems/${currentProblem.id}/grade`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ score, feedback })
        });

        // Load next problem
        loadNextProblem();
    } catch (error) {
        console.error('Failed to submit grade:', error);
        alert('Failed to submit grade');
    }
}

// Load statistics
async function loadStatistics() {
    try {
        const response = await fetch(`${API_BASE}/sessions/${currentSession.id}/stats`);
        const stats = await response.json();

        const container = document.getElementById('stats-container');
        container.innerHTML = `
            <div class="stat-card">
                <h3>Total Submissions</h3>
                <div class="value">${stats.total_submissions}</div>
            </div>
            <div class="stat-card">
                <h3>Problems Graded</h3>
                <div class="value">${stats.problems_graded} / ${stats.total_problems}</div>
            </div>
            <div class="stat-card">
                <h3>Progress</h3>
                <div class="value">${stats.progress_percentage.toFixed(1)}%</div>
            </div>
        `;

        // Add per-problem stats
        if (stats.problem_stats.length > 0) {
            const problemStatsHtml = stats.problem_stats.map(ps => `
                <div class="stat-card">
                    <h3>Problem ${ps.problem_number}</h3>
                    <div>Average: ${ps.avg_score ? ps.avg_score.toFixed(2) : 'N/A'}</div>
                    <div>Graded: ${ps.num_graded} / ${ps.num_total}</div>
                </div>
            `).join('');
            container.innerHTML += problemStatsHtml;
        }
    } catch (error) {
        console.error('Failed to load statistics:', error);
    }
}

// Finalize and upload to Canvas
document.getElementById('finalize-btn').onclick = async () => {
    if (!confirm('Ready to finalize and upload to Canvas?')) return;

    try {
        // TODO: Implement finalization endpoint
        alert('Finalization not yet implemented');
    } catch (error) {
        console.error('Finalization failed:', error);
        alert('Finalization failed');
    }
};
