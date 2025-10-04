# Web Grading Interface

A modern web-based interface for grading exams, replacing the CSV-based manual grading workflow.

## Features

- 🚀 **Web-based workflow**: Upload → Name matching → Grading → Canvas upload
- 📊 **Real-time statistics**: Track progress and per-problem performance
- 🔄 **Persistent sessions**: Resume grading anytime, crash recovery built-in, export/import checkpoints
- 🎯 **Problem-first grading**: Grade all Q1, then Q2, etc. with intelligent ordering (blanks at end)
- 🔒 **Anonymous by default**: Student names hidden during grading
- 💾 **Local storage**: SQLite database for FERPA compliance
- 🤖 **AI-powered features**: Name extraction, blank detection, handwriting transcription
- 🔁 **Duplicate detection**: SHA256 hashing prevents re-processing same files
- ⚡ **Keyboard shortcuts**: Fast grading with 0-9, Enter, Shift+Tab, and Back navigation
- 🎚️ **Max points management**: Set once per problem, remembers across all students
- 🔀 **Canvas environment switching**: Test in dev, upload to prod

## Architecture

```
web_grading/
├── docs/              # Documentation
├── web_api/           # FastAPI backend
│   ├── routes/        # API endpoints
│   └── services/      # Business logic
├── web_frontend/      # Vanilla JS frontend
└── docker/           # Deployment configs
```

## Quick Start

### Prerequisites

- Python 3.8+
- pip

### Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Start the server:
```bash
cd Autograder/web_grading
python -m web_api.main
```

3. Open browser:
```
http://localhost:8000
```

**Note:** The web interface uses the same `~/.env` file as the main grading flow. It defaults to non-prod Canvas credentials (`CANVAS_API_KEY` and `CANVAS_API_URL`) for safety during development.

**Switching to Production Canvas:**
To use production Canvas, add this to your `~/.env`:
```bash
USE_PROD_CANVAS=true
CANVAS_API_KEY_PROD=your_prod_key
CANVAS_API_URL_PROD=https://csumb.instructure.com
```

## Usage

### 1. Create Session

- Click "+ Create New Session"
- Select Canvas environment (Development or Production)
- Choose course from dropdown
- Select assignment from dropdown
- **Optional**: Override Canvas points for assignment
- System creates session and prepares for upload

**Resuming Sessions**: Click on any existing session to continue grading or review finalized grades

### 2. Upload Exams

- Drag and drop PDF files or a ZIP containing PDFs
- **Duplicate detection**: System automatically skips files you've already uploaded (by SHA256 hash)
- **Automatic processing**:
  - Name extraction using Claude AI
  - Fuzzy matching to Canvas roster
  - Problem splitting by horizontal divider lines
  - Blank detection (heuristic-based)
  - Name redaction from problem images
- **Progress tracking**: Real-time upload and processing status
- **Add more later**: Use "+ Upload More Exams" button to add late submissions

### 3. Name Matching (if needed)

- Review AI-extracted names with confidence scores
- See name image crops for verification
- Manually assign to correct Canvas students from dropdown
- **Smart filtering**: Students already matched are excluded from dropdown

### 4. Grade Problems

**Navigation**:
- Select problem number from dropdown
- Use "← Back" button to review previous submission
- Switch between problem numbers anytime
- History tracking: Navigate backward through your grading session

**Grading**:
- **Set max points**: Defaults to 8, set once per problem number (persists for all students)
- **Score entry**: Use slider or type directly (0-9 keyboard shortcut)
- **Feedback**: Optional text feedback for students
- **Blank detection**: Auto-populated with 0 score if detected blank
- **Problem order**: Non-blank problems first, then blank-detected ones at end

**Keyboard Shortcuts**:
- `0-9`: Quick score entry
- `Enter`: Submit grade and move to next
- `Shift+Tab`: Skip problem without grading
- Displayed below buttons for reference

**Progress Display**:
- Shows "X / Y" (graded / total) for current problem number
- Shows "(N blank)" when ungraded blank problems remain
- Overall progress bar across all problems

### 5. Review & Finalize

**Statistics**:
- View per-problem stats: average, min, max scores
- See grading progress per problem
- Track overall completion percentage
- Click "Continue Grading" to resume grading

**Session Management**:
- **Export Session**: Download JSON checkpoint for backup
- **Import Session**: Resume from exported checkpoint
- **Change Canvas Target**: Switch from dev to prod before finalizing

**Finalization**:
- Click "Finalize & Upload to Canvas"
- Confirms Canvas target (course, assignment, environment)
- Shows real-time upload progress
- Uploads all grades to Canvas
- Marks session as "finalized"

## API Documentation

Once running, view auto-generated API docs at:
- Swagger UI: http://localhost:8000/api/docs
- ReDoc: http://localhost:8000/api/redoc

## Database

SQLite database is stored at `~/.autograder/grading.db` by default.

Override with environment variable:
```bash
export GRADING_DB_PATH=/path/to/grading.db
python -m web_api.main
```

### Schema Version

Current schema version: **10**

The database includes automatic schema versioning and migration support. Migrations are applied automatically on startup.

**Key tables**:
- `grading_sessions`: Session metadata (course, assignment, Canvas config)
- `submissions`: Student exam submissions (with file hash for deduplication)
- `problems`: Individual problem instances (with blank detection metadata)
- `problem_metadata`: Per-problem settings (max_points)

**Recent migrations**:
- v8: Added min/max score tracking (now computed dynamically)
- v9: Added max_points to problems table
- v10: Created problem_metadata table for session-level max_points storage

## Development

### Project Structure

```
web_api/
├── main.py           # FastAPI app entry point
├── models.py         # Pydantic request/response models
├── database.py       # SQLite connection & schema migrations
├── routes/           # API endpoint handlers
│   ├── sessions.py   # Session CRUD, export/import, Canvas config
│   ├── problems.py   # Problem grading, navigation (next/previous)
│   ├── uploads.py    # File upload & background processing
│   ├── matching.py   # Manual name matching
│   ├── canvas.py     # Canvas API integration (courses, assignments)
│   └── finalize.py   # Grade finalization & Canvas upload
└── services/         # Business logic (reusable)
    └── exam_processor.py  # PDF processing, splitting, blank detection

web_frontend/
├── index.html        # Single-page app
├── css/
│   └── style.css     # Styling
└── js/
    ├── app.js        # Main app, session management, navigation
    ├── grading.js    # Grading interface, keyboard shortcuts, history
    └── matching.js   # Name matching UI
```

### Running Tests

```bash
pytest tests/
```

### Code Style

```bash
black web_api/
flake8 web_api/
```

## Extending the System

### Adding a New API Endpoint

1. Create or update a route file in `web_api/routes/`:

```python
# web_api/routes/my_feature.py
from fastapi import APIRouter, HTTPException
from ..database import get_db_connection
from ..models import MyRequest, MyResponse

router = APIRouter()

@router.post("/my-endpoint", response_model=MyResponse)
async def my_endpoint(request: MyRequest):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Your logic here
        cursor.execute("SELECT * FROM my_table WHERE id = ?", (request.id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Not found")

        return MyResponse(field=row["field"])
```

2. Register the router in `web_api/main.py`:

```python
from .routes import my_feature
app.include_router(my_feature.router, prefix="/api/my-feature", tags=["my-feature"])
```

### Adding a Database Migration

1. Add a migration function to `web_api/database.py`:

```python
def migrate_to_v11(cursor):
    """Add new table for feature X"""
    log.info("Migrating to schema version 11: adding feature X table")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS my_new_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES grading_sessions(id)
        )
    """)
```

2. Update the version and migrations dict:

```python
CURRENT_SCHEMA_VERSION = 11

MIGRATIONS = {
    # ... existing migrations ...
    11: migrate_to_v11,
}
```

3. Test by deleting `grading.db` and restarting server

### Adding a Pydantic Model

In `web_api/models.py`:

```python
class MyRequest(BaseModel):
    """Request model for my feature"""
    id: int
    optional_field: Optional[str] = None

class MyResponse(BaseModel):
    """Response model for my feature"""
    field: str
    computed_value: int

    class Config:
        from_attributes = True  # Allows creating from SQLite Row objects
```

### Adding a Frontend Feature

1. Add HTML section in `web_frontend/index.html`:

```html
<section id="my-section" class="section">
    <h2>My Feature</h2>
    <div id="my-content"></div>
    <button id="my-action-btn" class="btn btn-primary">Do Action</button>
</section>
```

2. Add JavaScript in `web_frontend/js/app.js` or new file:

```javascript
// Initialize your feature
function initializeMyFeature() {
    document.getElementById('my-action-btn').onclick = async () => {
        try {
            const response = await fetch(`${API_BASE}/my-endpoint`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: 123 })
            });

            const data = await response.json();
            document.getElementById('my-content').textContent = data.field;
        } catch (error) {
            console.error('Failed:', error);
            alert('Action failed: ' + error.message);
        }
    };
}

// Call when navigating to section
function navigateToMyFeature() {
    navigateToSection('my-section');
    initializeMyFeature();
}
```

3. Add navigation logic to switch to your section

### Background Task Pattern

For long-running operations (like file processing):

```python
from fastapi import BackgroundTasks

@router.post("/start-task")
async def start_task(background_tasks: BackgroundTasks):
    # Immediately return response
    background_tasks.add_task(long_running_task, arg1, arg2)
    return {"status": "processing"}

async def long_running_task(arg1, arg2):
    # This runs in background
    try:
        # Update database with progress
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE tasks SET progress = ? WHERE id = ?", (50, task_id))

        # Do work...

    except Exception as e:
        # Handle errors, update status
        log.error(f"Task failed: {e}")
```

**Note**: Currently, Ctrl-C may not immediately stop background tasks. This is a known limitation of uvicorn/FastAPI.

### Configuring Features

Key settings in `exam_processor.py`:

```python
# Blank detection sensitivity
blank_confidence_threshold=0.8  # 0-1, higher = more confident required

# AI usage
use_ai_for_borderline=False  # Use AI for low-confidence blanks
extract_max_points_enabled=False  # Auto-extract max points (currently disabled)

# Problem detection
min_region_height=100  # Minimum pixels for a problem region
```

## Deployment

### Docker Compose

```bash
cd docker
docker-compose up
```

This will start:
- FastAPI backend on port 8000
- Frontend served via nginx on port 3000

### Production Considerations

- Use proper WSGI server (uvicorn with workers)
- Set up HTTPS if exposing externally
- Configure backups for SQLite database
- Set appropriate file upload limits

## Roadmap

See [docs/todo.md](docs/todo.md) for planned features:

- ✅ Core grading workflow
- ⏳ Drawing annotations (high priority)
- ⏳ FERPA anonymization with hashed names
- ⏳ Cross-exam question tracking
- ⏳ Student performance filtering

## Architecture Documentation

See [docs/architecture.md](docs/architecture.md) for detailed technical documentation.

## Troubleshooting

### Server won't start

- Check port 8000 is not in use: `lsof -i :8000`
- Verify Python version: `python --version` (3.8+)
- Check dependencies: `pip install -r requirements.txt`

### Database errors

- Reset database: `rm ~/.autograder/grading.db`
- Check permissions on database directory

### Upload failures

- Check file size limits (default: 100MB)
- Verify PDFs are valid (try opening manually)
- Check disk space

## Contributing

1. Create feature branch
2. Make changes
3. Add tests
4. Update documentation
5. Submit PR

## License

Same as parent Autograder project.
