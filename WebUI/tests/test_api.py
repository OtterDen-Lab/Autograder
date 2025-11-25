"""
Basic API tests to verify setup.
"""
import pytest
from fastapi.testclient import TestClient
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from web_api.main import app


@pytest.fixture
def client():
  """Create test client"""
  return TestClient(app)


def test_health_check(client):
  """Test health endpoint"""
  response = client.get("/api/health")
  assert response.status_code == 200
  data = response.json()
  assert data["status"] == "healthy"
  assert "version" in data


def test_create_session(client):
  """Test session creation"""
  response = client.post("/api/sessions",
                         json={
                           "course_id": 12345,
                           "assignment_id": 67890,
                           "assignment_name": "Test Exam"
                         })
  assert response.status_code == 200
  data = response.json()
  assert data["assignment_name"] == "Test Exam"
  assert data["status"] == "preprocessing"


def test_get_nonexistent_session(client):
  """Test getting non-existent session returns 404"""
  response = client.get("/api/sessions/99999")
  assert response.status_code == 404


def test_list_sessions(client):
  """Test listing sessions"""
  # Create a session first
  client.post("/api/sessions",
              json={
                "course_id": 12345,
                "assignment_id": 67890,
                "assignment_name": "Test Exam"
              })

  # List sessions
  response = client.get("/api/sessions")
  assert response.status_code == 200
  data = response.json()
  assert isinstance(data, list)
  assert len(data) > 0
