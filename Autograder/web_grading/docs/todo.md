# Web Grading Interface - Future Work & Enhancements

## High Priority

### Drawing Annotations (iPad-like Experience)
**Goal**: Replicate the current iPad PDF annotation workflow in the web interface

**Requirements:**
- Canvas-based drawing tools (pen, highlighter, shapes)
- Save annotations as overlay images
- Composite annotations onto PDFs during finalization
- Touch/stylus support for tablet grading

**Technical Approach:**
- Use Fabric.js or similar canvas library
- Store drawing data as base64 PNG in database
- Layer annotations on top of problem images in grader UI
- Merge annotation layers with PDF pages during export

**Status**: Not started

---

## Medium Priority

### FERPA-Compliant Anonymization
**Goal**: Support hashed student names for enhanced privacy compliance

**Requirements:**
- Hash student names with session-specific salt
- Display only hashes during grading
- Store mapping in separate table
- Reveal real names only during finalization or with explicit permission

**Database Changes:**
```sql
CREATE TABLE name_hashes (
    id INTEGER PRIMARY KEY,
    hash TEXT UNIQUE NOT NULL,
    student_name TEXT NOT NULL,
    salt TEXT NOT NULL,
    session_id INTEGER,
    FOREIGN KEY (session_id) REFERENCES grading_sessions(id)
);
```

**Status**: Not started

---

### Cross-Exam Question Tracking
**Goal**: Track question performance across multiple exams and semesters

**Requirements:**
- Assign problem_type identifiers to questions (e.g., "fork_basics")
- Track question versions (v1, v2, etc.)
- Store variation seeds for randomized questions
- Historical statistics dashboard

**Database Changes:**
```sql
CREATE TABLE problem_metadata (
    id INTEGER PRIMARY KEY,
    problem_id INTEGER NOT NULL,
    problem_type TEXT,
    problem_version INTEGER,
    problem_seed TEXT,
    FOREIGN KEY (problem_id) REFERENCES problems(id)
);

CREATE TABLE historical_stats (
    id INTEGER PRIMARY KEY,
    problem_type TEXT,
    semester TEXT,
    avg_score REAL,
    num_attempts INTEGER
);
```

**Status**: Not started

---

### Student Performance Filtering
**Goal**: Identify struggling students or potential collaboration

**Features:**
- Filter problems by performance threshold (e.g., "students scoring <70%")
- Flag similar responses across submissions (collaboration detection)
- Anonymous filtering (maintain privacy during grading)
- Post-grading analysis tools

**Technical Approach:**
- Add query parameters to problem endpoints
- Similarity scoring for response comparison (future: ML-based)
- Statistical outlier detection

**Status**: Not started

---

## Low Priority / Nice-to-Have

### WebSocket Support
**Current**: Server-Sent Events (SSE) for one-way status updates
**Future**: WebSockets for bidirectional communication

**Use Cases:**
- Real-time collaborative grading (multiple graders)
- Live updates when database changes externally
- Interactive problem navigation

**Status**: Not needed yet (SSE sufficient for current scope)

---

### Advanced Rubric Support
**Goal**: Support custom rubrics beyond simple point scores

**Features:**
- Bucket-based grading (e.g., "perfect", "good", "needs work")
- Multi-criterion rubrics with weighted scores
- Rubric templates per question type

**Status**: Not started

---

### Multi-Grader Session Support
**Goal**: Multiple graders working on same exam simultaneously

**Requirements:**
- Problem locking (prevent two graders on same problem)
- Real-time synchronization
- Grader-specific statistics and progress tracking

**Technical Challenges:**
- Concurrent database access
- Conflict resolution
- WebSocket coordination

**Status**: Future consideration

---

### Mobile/Tablet Native App
**Goal**: iOS/Android app using same API

**Benefits:**
- Better stylus support for annotations
- Offline grading capability
- Native touch gestures

**Status**: Future consideration (web interface should work on tablets first)

---

### AI-Assisted Grading
**Goal**: Use AI to suggest scores or identify common patterns

**Features:**
- Pre-score suggestions based on similarity to graded responses
- Common mistake detection
- Auto-categorization of response types

**Technical Approach:**
- Embeddings-based similarity (OpenAI/Anthropic)
- Pattern matching on text-based responses
- Human-in-the-loop validation

**Status**: Future research

---

## Infrastructure & Maintenance

### Database Migrations
- ✅ Schema versioning system
- Automatic migration runner
- Rollback capability
- Migration testing

### Testing Suite
- Unit tests for API endpoints
- Integration tests for full workflow
- Frontend E2E tests (Playwright/Cypress)
- Performance benchmarks

### Documentation
- API reference (auto-generated from FastAPI)
- User guide with screenshots
- Video tutorials
- Deployment guide for other instructors

---

## Completed Items

_(Items will be moved here as they're implemented)_

---

## Notes & Ideas

### Alternative Workflows
- Support for group assignments (multiple students per submission)
- Peer grading integration
- Self-assessment tools

### Export Formats
- Export grading statistics as CSV/Excel
- PDF reports with visualizations
- LMS-agnostic export (not just Canvas)

### Question Bank Integration
- Connect to quiz generator for question metadata
- Auto-populate problem_type from generator
- Bidirectional sync (grading insights → question improvements)
