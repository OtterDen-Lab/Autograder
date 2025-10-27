// Feedback Tags Functionality
// Allows creating and applying reusable feedback comments for common mistakes

let feedbackTags = []; // Array of tag objects {id, short_name, comment_text, use_count}
let selectedTagIds = new Set(); // Set of selected tag IDs

// =============================================================================
// LOAD AND DISPLAY TAGS
// =============================================================================

async function loadFeedbackTags(sessionId, problemNumber) {
    try {
        const response = await fetch(`${API_BASE}/feedback-tags/${sessionId}/${problemNumber}`);

        if (!response.ok) {
            throw new Error('Failed to load feedback tags');
        }

        feedbackTags = await response.json();
        displayFeedbackTags();

        // Show the tags container if we're on the grading page
        const container = document.getElementById('feedback-tags-container');
        if (container) {
            container.style.display = 'block';
        }

    } catch (error) {
        console.error('Failed to load feedback tags:', error);
        // Don't show error to user - just log it
        feedbackTags = [];
        displayFeedbackTags();
    }
}

function displayFeedbackTags() {
    const tagsList = document.getElementById('tags-list');
    const applyBtn = document.getElementById('apply-tags-btn');

    if (!tagsList) return;

    // Clear current tags
    tagsList.innerHTML = '';

    if (feedbackTags.length === 0) {
        // Show placeholder
        const placeholder = document.createElement('span');
        placeholder.className = 'tags-placeholder';
        placeholder.textContent = 'No tags yet. Click "+ Add Tag" to create reusable feedback comments.';
        tagsList.appendChild(placeholder);
        applyBtn.style.display = 'none';
    } else {
        // Display tag buttons
        feedbackTags.forEach(tag => {
            const tagButton = createTagButton(tag);
            tagsList.appendChild(tagButton);
        });
    }
}

function createTagButton(tag) {
    const button = document.createElement('button');
    button.className = 'tag-button';
    button.dataset.tagId = tag.id;
    button.dataset.commentText = tag.comment_text;

    // Add selected class if this tag is selected
    if (selectedTagIds.has(tag.id)) {
        button.classList.add('selected');
    }

    // Tag name span
    const nameSpan = document.createElement('span');
    nameSpan.textContent = tag.short_name;
    nameSpan.title = tag.comment_text; // Show full comment on hover

    // Use count badge (if > 0)
    if (tag.use_count > 0) {
        const badge = document.createElement('span');
        badge.textContent = `(${tag.use_count})`;
        badge.style.cssText = 'font-size: 11px; opacity: 0.7;';
        nameSpan.appendChild(document.createTextNode(' '));
        nameSpan.appendChild(badge);
    }

    // Delete button
    const deleteBtn = document.createElement('span');
    deleteBtn.className = 'tag-delete';
    deleteBtn.textContent = '×';
    deleteBtn.title = 'Delete tag';
    deleteBtn.onclick = (e) => {
        e.stopPropagation();
        deleteTag(tag.id, tag.short_name);
    };

    button.appendChild(nameSpan);
    button.appendChild(deleteBtn);

    // Toggle selection on click
    button.onclick = (e) => {
        // Don't toggle if clicking delete button
        if (e.target.classList.contains('tag-delete')) return;

        toggleTagSelection(tag.id);
    };

    return button;
}

// =============================================================================
// TAG SELECTION
// =============================================================================

function toggleTagSelection(tagId) {
    const button = document.querySelector(`.tag-button[data-tag-id="${tagId}"]`);
    if (!button) return;

    if (selectedTagIds.has(tagId)) {
        selectedTagIds.delete(tagId);
        button.classList.remove('selected');
    } else {
        selectedTagIds.add(tagId);
        button.classList.add('selected');
    }

    // Show/hide apply button based on selection
    const applyBtn = document.getElementById('apply-tags-btn');
    if (applyBtn) {
        applyBtn.style.display = selectedTagIds.size > 0 ? 'block' : 'none';
    }
}

function clearTagSelection() {
    selectedTagIds.clear();
    document.querySelectorAll('.tag-button.selected').forEach(btn => {
        btn.classList.remove('selected');
    });

    const applyBtn = document.getElementById('apply-tags-btn');
    if (applyBtn) {
        applyBtn.style.display = 'none';
    }
}

// =============================================================================
// APPLY TAGS TO FEEDBACK
// =============================================================================

async function applySelectedTags() {
    const feedbackInput = document.getElementById('feedback-input');
    if (!feedbackInput) return;

    if (selectedTagIds.size === 0) {
        return; // Nothing to apply
    }

    // Get comments for selected tags
    const selectedComments = feedbackTags
        .filter(tag => selectedTagIds.has(tag.id))
        .map(tag => tag.comment_text);

    if (selectedComments.length === 0) return;

    // Format as bullet points
    const bulletPoints = selectedComments.map(comment => `• ${comment}`).join('\n');

    // Append to existing feedback (with blank line if feedback already exists)
    const currentFeedback = feedbackInput.value.trim();
    if (currentFeedback) {
        feedbackInput.value = `${currentFeedback}\n\n${bulletPoints}`;
    } else {
        feedbackInput.value = bulletPoints;
    }

    // Increment use count for selected tags
    const incrementPromises = Array.from(selectedTagIds).map(tagId =>
        fetch(`${API_BASE}/feedback-tags/${tagId}/use`, { method: 'POST' })
    );

    try {
        await Promise.all(incrementPromises);

        // Reload tags to update use counts
        if (currentSession && currentProblemNumber) {
            await loadFeedbackTags(currentSession.id, currentProblemNumber);
        }
    } catch (error) {
        console.error('Failed to increment tag usage:', error);
        // Non-critical error - don't block user
    }

    // Clear selection after applying
    clearTagSelection();

    // Don't focus feedback textarea - let user continue with score input
}

// =============================================================================
// CREATE NEW TAG
// =============================================================================

function showAddTagDialog() {
    const dialog = document.getElementById('add-tag-dialog');
    if (!dialog) return;

    // Clear inputs
    document.getElementById('new-tag-name').value = '';
    document.getElementById('new-tag-comment').value = '';

    // Show dialog
    dialog.style.display = 'flex';

    // Focus name input
    document.getElementById('new-tag-name').focus();
}

function hideAddTagDialog() {
    const dialog = document.getElementById('add-tag-dialog');
    if (dialog) {
        dialog.style.display = 'none';
    }
}

async function createNewTag() {
    const nameInput = document.getElementById('new-tag-name');
    const commentInput = document.getElementById('new-tag-comment');

    if (!nameInput || !commentInput) return;

    const shortName = nameInput.value.trim();
    const commentText = commentInput.value.trim();

    // Validate inputs
    if (!shortName || shortName.length === 0) {
        alert('Please enter a short name for the tag');
        nameInput.focus();
        return;
    }

    if (shortName.length > 30) {
        alert('Short name must be 30 characters or less');
        nameInput.focus();
        return;
    }

    if (!commentText || commentText.length === 0) {
        alert('Please enter comment text');
        commentInput.focus();
        return;
    }

    if (commentText.length > 500) {
        alert('Comment text must be 500 characters or less');
        commentInput.focus();
        return;
    }

    // Create tag
    try {
        const saveBtn = document.getElementById('save-tag-btn');
        saveBtn.disabled = true;
        saveBtn.textContent = 'Creating...';

        const response = await fetch(`${API_BASE}/feedback-tags`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: currentSession.id,
                problem_number: currentProblemNumber,
                short_name: shortName,
                comment_text: commentText
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to create tag');
        }

        // Success - reload tags
        await loadFeedbackTags(currentSession.id, currentProblemNumber);

        // Close dialog
        hideAddTagDialog();

    } catch (error) {
        console.error('Failed to create tag:', error);
        alert(`Failed to create tag: ${error.message}`);
    } finally {
        const saveBtn = document.getElementById('save-tag-btn');
        saveBtn.disabled = false;
        saveBtn.textContent = 'Create Tag';
    }
}

// =============================================================================
// DELETE TAG
// =============================================================================

async function deleteTag(tagId, tagName) {
    // Confirm deletion
    if (!confirm(`Delete tag "${tagName}"?\n\nThis cannot be undone.`)) {
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/feedback-tags/${tagId}`, {
            method: 'DELETE'
        });

        if (!response.ok) {
            throw new Error('Failed to delete tag');
        }

        // Remove from selectedTagIds if it was selected
        selectedTagIds.delete(tagId);

        // Reload tags
        if (currentSession && currentProblemNumber) {
            await loadFeedbackTags(currentSession.id, currentProblemNumber);
        }

    } catch (error) {
        console.error('Failed to delete tag:', error);
        alert(`Failed to delete tag: ${error.message}`);
    }
}

// =============================================================================
// EVENT LISTENERS
// =============================================================================

// Add Tag button
document.getElementById('add-tag-btn')?.addEventListener('click', showAddTagDialog);

// Cancel button in dialog
document.getElementById('cancel-tag-btn')?.addEventListener('click', hideAddTagDialog);

// Save button in dialog
document.getElementById('save-tag-btn')?.addEventListener('click', createNewTag);

// Apply Tags button
document.getElementById('apply-tags-btn')?.addEventListener('click', applySelectedTags);

// Enter key in new tag form
document.getElementById('new-tag-name')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        e.preventDefault();
        document.getElementById('new-tag-comment').focus();
    }
});

document.getElementById('new-tag-comment')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.ctrlKey) {
        e.preventDefault();
        createNewTag();
    }
});

// Click outside dialog to close
document.getElementById('add-tag-dialog')?.addEventListener('click', (e) => {
    if (e.target.id === 'add-tag-dialog') {
        hideAddTagDialog();
    }
});
