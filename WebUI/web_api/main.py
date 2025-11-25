"""
Main FastAPI application entry point.
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from . import __version__
from .database import init_database
from .routes import sessions, problems, uploads, canvas, matching, finalize, ai_grader, alignment, feedback_tags, debug


@asynccontextmanager
async def lifespan(app: FastAPI):
  """Lifespan event handler for startup/shutdown"""
  # Startup: Initialize database
  init_database()

  yield

  # Shutdown: cleanup if needed
  pass


# Initialize FastAPI app
app = FastAPI(
  title="Web Grading API",
  description="API for web-based exam grading interface",
  version=__version__,
  docs_url="/api/docs",
  redoc_url="/api/redoc",
  lifespan=lifespan,
)

# CORS middleware for development
app.add_middleware(
  CORSMiddleware,
  allow_origins=["http://localhost:3000", "http://localhost:8000"],
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"],
)


# Health check endpoint (must be before static files mount)
@app.get("/api/health")
async def health_check():
  """Health check endpoint"""
  return {"status": "healthy", "version": __version__}


# Include routers
app.include_router(sessions.router,       prefix="/api/sessions",       tags=["sessions"])
app.include_router(problems.router,       prefix="/api/problems",       tags=["problems"])
app.include_router(uploads.router,        prefix="/api/uploads",        tags=["uploads"])
app.include_router(canvas.router,         prefix="/api/canvas",         tags=["canvas"])
app.include_router(matching.router,       prefix="/api/matching",       tags=["matching"])
app.include_router(finalize.router,       prefix="/api/finalize",       tags=["finalize"])
app.include_router(ai_grader.router,      prefix="/api/ai-grader",      tags=["ai-grader"])
app.include_router(alignment.router,      prefix="/api/alignment",      tags=["alignment"])
app.include_router(feedback_tags.router,  prefix="/api/feedback-tags",  tags=["feedback-tags"])
app.include_router(debug.router,          prefix="/api",                tags=["debug"])

# Mount static files (frontend) - MUST BE LAST as it catches all routes
frontend_path = Path(__file__).parent.parent / "web_frontend"
if frontend_path.exists():
  app.mount(
    "/",
    StaticFiles(directory=str(frontend_path), html=True),
    name="static"
  )

if __name__ == "__main__":
  import uvicorn
  uvicorn.run("web_api.main:app", host="127.0.0.1", port=8000, reload=True)
