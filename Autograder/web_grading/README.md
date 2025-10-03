# Web Grading Interface

A modern web-based interface for grading exams, replacing the CSV-based manual grading workflow.

## Features

- 🚀 **Web-based workflow**: Upload → Name matching → Grading → Canvas upload
- 📊 **Real-time statistics**: Track progress and per-problem performance
- 🔄 **Persistent sessions**: Resume grading anytime, crash recovery built-in
- 🎯 **Problem-first grading**: Grade all Q1, then Q2, etc. (configurable)
- 🔒 **Anonymous by default**: Student names hidden during grading
- 💾 **Local storage**: SQLite database for FERPA compliance

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

## Usage

### 1. Create Session

- Enter Canvas course ID and assignment ID
- Provide assignment name

### 2. Upload Exams

- Drag and drop PDF files or a zip containing PDFs
- Wait for preprocessing (name extraction, shuffling, splitting)

### 3. Name Matching (if needed)

- Review and confirm auto-matched students
- Manually match any unrecognized names

### 4. Grade Problems

- Select problem number
- Grade each student's response (random order)
- Provide score and optional feedback
- System auto-advances to next problem

### 5. Review & Finalize

- View statistics and grade distribution
- Finalize to merge PDFs and upload to Canvas

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

Current schema version: **1**

The database includes automatic schema versioning and migration support.

## Development

### Project Structure

```
web_api/
├── main.py           # FastAPI app entry point
├── models.py         # Pydantic request/response models
├── database.py       # SQLite connection & schema
├── routes/           # API endpoint handlers
│   ├── sessions.py   # Session CRUD
│   ├── problems.py   # Problem grading
│   └── uploads.py    # File upload & processing
└── services/         # Business logic (reusable)
    ├── exam_processor.py  # PDF processing
    └── name_matcher.py    # Student matching
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
