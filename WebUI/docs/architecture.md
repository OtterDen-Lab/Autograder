# Web Grading Interface - Architecture Documentation

## Overview

The web grading interface replaces the CSV-based manual grading workflow with a persistent web service that provides a complete grading experience: upload → name matching → grading → Canvas upload.

## System Architecture

### High-Level Design

```
┌─────────────┐         ┌──────────────────┐         ┌─────────────┐
│   Browser   │ <──────>│   FastAPI Server │ <──────>│   SQLite    │
│  (Vanilla   │   HTTP  │   (web_api/)     │         │  Database   │
│    JS)      │   SSE   │                  │         │             │
└─────────────┘         └──────────────────┘         └─────────────┘
                               │
                               ▼
                        ┌──────────────────┐
                        │  Exam Processor  │
                        │   (services/)    │
                        │  - PDF handling  │
                        │  - Name matching │
                        │  - Canvas upload │
                        └──────────────────┘
```

### Technology Stack

**Backend:**
- **FastAPI**: Web framework with async support, SSE, automatic API docs
- **SQLite**: Local database with schema versioning
- **Pydantic**: Data validation and serialization
- **PyMuPDF (fitz)**: PDF processing (reused from existing code)

**Frontend:**
- **Vanilla JavaScript**: No build step, easy to understand and modify
- **Modern CSS**: Responsive design for desktop and tablet use
- **Server-Sent Events (SSE)**: Real-time status updates during processing

**Deployment:**
- **Development**: `uvicorn` dev server
- **Production**: Docker Compose (FastAPI + nginx + SQLite volume)

## Database Schema

### Core Tables

```sql
-- Schema version tracking
CREATE TABLE _schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Grading sessions
CREATE TABLE grading_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL,
    assignment_name TEXT NOT NULL,
    course_id INTEGER NOT NULL,
    course_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL,  -- 'preprocessing', 'name_matching_needed', 'ready', 'grading', 'complete'
    canvas_points REAL,    -- Override for Canvas assignment points
    metadata TEXT          -- JSON for flexible future fields
);

-- Student submissions
CREATE TABLE submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    student_name TEXT,              -- Real name for Canvas
    display_name TEXT,              -- Shown in UI (for anonymization)
    canvas_user_id INTEGER,
    page_mappings TEXT NOT NULL,    -- JSON array of page order
    total_score REAL,
    graded_at TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES grading_sessions(id)
);

-- Individual problems/questions
CREATE TABLE problems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    submission_id INTEGER NOT NULL,
    problem_number INTEGER NOT NULL,  -- Question number (1, 2, 3, etc.)
    image_data TEXT NOT NULL,         -- Base64 encoded PNG from PDF
    score REAL,
    feedback TEXT,
    graded BOOLEAN DEFAULT 0,
    graded_at TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES grading_sessions(id),
    FOREIGN KEY (submission_id) REFERENCES submissions(id),
    INDEX idx_session_problem (session_id, problem_number),
    INDEX idx_graded (session_id, graded)
);

-- Problem statistics (computed)
CREATE TABLE problem_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    problem_number INTEGER NOT NULL,
    avg_score REAL,
    num_graded INTEGER,
    num_total INTEGER,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES grading_sessions(id),
    UNIQUE(session_id, problem_number)
);
```

### Future Extensions (tracked in todo.md)

```sql
-- For FERPA-compliant anonymization
CREATE TABLE name_hashes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hash TEXT UNIQUE NOT NULL,
    salt TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- For cross-exam question tracking
CREATE TABLE problem_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_id INTEGER NOT NULL,
    problem_type TEXT,     -- e.g., "fork_basics"
    problem_seed TEXT,     -- Variation identifier
    problem_version INTEGER,
    FOREIGN KEY (problem_id) REFERENCES problems(id)
);
```

## API Specification

### REST Endpoints

**Session Management:**
```
POST   /api/sessions
GET    /api/sessions/{id}
GET    /api/sessions/{id}/status
DELETE /api/sessions/{id}
```

**File Upload & Processing:**
```
POST   /api/sessions/{id}/upload       - Upload exam PDFs/zip
GET    /api/sessions/{id}/events       - SSE endpoint for status updates
```

**Name Matching:**
```
GET    /api/sessions/{id}/unmatched    - Get submissions needing name match
POST   /api/sessions/{id}/match        - Submit name matches
```

**Grading:**
```
GET    /api/sessions/{id}/problems/{num}/next  - Get next ungraded problem
POST   /api/problems/{id}/grade                - Submit grade for problem
GET    /api/sessions/{id}/stats                - Get grading statistics
```

**Finalization:**
```
POST   /api/sessions/{id}/finalize     - Merge PDFs and upload to Canvas
GET    /api/sessions/{id}/export       - Export session database
```

### Request/Response Models (Pydantic)

```python
class SessionCreate(BaseModel):
    course_id: int
    assignment_id: int
    assignment_name: str
    canvas_points: Optional[float] = None

class SessionStatus(BaseModel):
    id: int
    status: str
    progress: Optional[float]  # 0.0 to 1.0
    message: Optional[str]

class ProblemResponse(BaseModel):
    id: int
    problem_number: int
    image_data: str  # Base64 PNG
    current_index: int
    total_count: int

class GradeSubmission(BaseModel):
    score: float = Field(ge=0)
    feedback: Optional[str] = None
```

## Workflow States

### Session State Machine

```
[Create Session]
       ↓
[preprocessing] ────────────────┐
       ↓                         │
[name_matching_needed] ─── OR ──┤
       ↓                         │
[ready] ←──────────────────────┘
       ↓
[grading]
       ↓
[complete]
```

### User Journey

1. **Start Session**: Visit http://localhost:8000
2. **Upload**: Drag-drop exam folder/zip
3. **Processing**: Watch SSE progress updates (PDF splitting, name extraction)
4. **Name Matching** (if needed): Interactive UI to match unrecognized names
5. **Grading**: Problem-first workflow
   - Select problem number (defaults to 1)
   - Grade each student's response (served in random order)
   - Auto-advance to next problem when complete
6. **Review**: Statistics dashboard
7. **Finalize**: Merge PDFs, upload to Canvas (with dev/prod option)

## Code Organization

### Directory Structure

```
web_grading/
├── docs/                      # Documentation
│   ├── planning-notes.md      # Original planning
│   ├── architecture.md        # This file
│   ├── todo.md               # Future work
│   └── api-spec.md           # Detailed API docs
├── web_api/                  # FastAPI backend
│   ├── __init__.py
│   ├── main.py              # App entry point
│   ├── models.py            # Pydantic models
│   ├── database.py          # SQLite connection & migrations
│   ├── routes/              # API endpoints
│   │   ├── sessions.py
│   │   ├── problems.py
│   │   ├── uploads.py
│   │   └── events.py        # SSE endpoints
│   └── services/            # Business logic (reusable)
│       ├── exam_processor.py  # Extract from Assignment__Exam
│       ├── name_matcher.py    # Student name matching
│       └── canvas_uploader.py # Finalization & upload
├── web_frontend/             # Vanilla JS frontend
│   ├── index.html
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── app.js           # Main application
│       └── grading.js       # Grading interface
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
└── tests/
    ├── test_api.py
    ├── test_exam_processor.py
    └── test_database.py
```

### Service Extraction Strategy

The current `Assignment__Exam` class contains all exam processing logic. We'll extract reusable components:

```python
# Before (monolithic)
class Assignment__Exam(Assignment):
    def prepare(self, ...):
        # PDF processing
        # Name matching
        # Shuffling
        # Redaction
        ...

# After (service-oriented)
class ExamProcessor:
    """Reusable exam processing logic"""
    def process_pdfs(self, input_dir) -> List[ProcessedExam]: ...
    def extract_names(self, pdf_path) -> str: ...
    def redact_and_split(self, pdf_path, page_ranges) -> List[Document]: ...
    def merge_pages(self, pages, mappings) -> Document: ...

class NameMatcher:
    """Student name matching logic"""
    def auto_match(self, submissions, students) -> Tuple[Matched, Unmatched]: ...
    def fuzzy_match(self, name1, name2) -> float: ...

# Both old CLI and new web API can use these services
```

## Security & Privacy

### FERPA Compliance

- **Local Storage**: SQLite database stored in user-controlled directory
- **Data Ownership**: User owns and manages .sqlite file
- **Anonymization**: Display names can be hashed (future enhancement)
- **No Cloud Storage**: All processing happens locally

### Access Control

- **Local-only**: Server binds to localhost (not exposed externally)
- **Future**: Add authentication if multi-user deployment needed

## Performance Considerations

### Image Storage

- **Base64 in SQLite**: Simpler, portable, acceptable for ~100 students
- **Future**: File-based storage if performance becomes an issue

### Database Sizing

Rough estimates for 50 students, 10 problems:
- Images: ~500 KB each × 500 = ~250 MB
- Metadata: Negligible
- Total: ~300 MB database file (acceptable)

## Deployment

### Development

```bash
cd Autograder/web_grading
uvicorn web_api.main:app --reload --port 8000
```

### Production (Docker Compose)

```yaml
version: '3.8'
services:
  api:
    build: ./web_api
    ports:
      - "8000:8000"
    volumes:
      - ./grading_data:/app/data

  frontend:
    image: nginx:alpine
    ports:
      - "3000:80"
    volumes:
      - ./web_frontend:/usr/share/nginx/html:ro
```

## Migration Path

### Phase 1: Foundation
- ✅ Project structure
- ✅ Documentation
- Extract exam processing services
- Set up FastAPI skeleton

### Phase 2: Backend
- Implement database layer
- Build REST API endpoints
- Add SSE for real-time updates

### Phase 3: Frontend
- Upload interface
- Name matching UI
- Grading interface
- Statistics dashboard

### Phase 4: Integration
- End-to-end testing
- Docker deployment
- User documentation

### Phase 5: Enhancements
- Drawing annotations
- Advanced anonymization
- Cross-exam tracking
