"""
Canvas API integration endpoints.
"""
from fastapi import APIRouter, HTTPException
import os
import sys
from pathlib import Path

# Add parent Autograder to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

router = APIRouter()


@router.get("/courses")
async def list_courses(use_prod: bool = False):
  """List all active courses for the current user"""
  try:
    canvas_interface = get_canvas_interface(use_prod=use_prod)

    # Get all active courses, include additional fields for sorting
    courses = canvas_interface.canvas.get_courses(
      enrollment_state='active',
      enrollment_type='teacher',  # Only courses where user is a teacher
      include=['term', 'favorites']  # Include term and favorites information
    )

    # Determine environment label
    is_dev = "beta" in canvas_interface.canvas_url or "test" in canvas_interface.canvas_url
    env_label = "DEV" if is_dev else "PROD"

    # Convert to list with metadata for sorting
    course_list = []
    for course in courses:
      # Only include courses with a name and workflow_state = 'available'
      if hasattr(course, 'name'):
        # Extract start_at and enrollment_term_id if available
        start_at = getattr(course, 'start_at', None)
        term_id = getattr(course, 'enrollment_term_id', None)
        workflow_state = getattr(course, 'workflow_state', None)
        is_favorite = getattr(course, 'is_favorite', False)

        # Only include available (not deleted/completed) courses
        if workflow_state == 'available':
          course_list.append({
            "id": course.id,
            "name": course.name,
            "start_at": start_at,
            "enrollment_term_id": term_id,
            "is_favorite": is_favorite,
          })

    # Sort by: favorites first, then by term ID (highest first), then by start date (newest first)
    # Courses without start_at go to the end
    course_list.sort(
      key=lambda c: (
        c['is_favorite'],  # Favorites first (True > False)
        c['enrollment_term_id'] or 0,  # Higher term IDs first
        c['start_at'] or ''  # Then by start date (newer first)
      ),
      reverse=True)

    return {"courses": course_list, "environment": env_label}

  except Exception as e:
    raise HTTPException(status_code=500,
                        detail=f"Failed to fetch courses: {str(e)}")


@router.get("/courses/{course_id}/assignments")
async def list_assignments(course_id: int, use_prod: bool = False):
  """List all assignments for a course"""
  import logging
  log = logging.getLogger(__name__)

  try:
    canvas_interface = get_canvas_interface(use_prod=use_prod)

    # Get the raw Canvas course object directly from the canvasapi library
    canvas_course = canvas_interface.canvas.get_course(course_id)

    # Get all assignments
    assignments = canvas_course.get_assignments()

    # Convert to simple list
    assignment_list = []
    for assignment in assignments:
      assignment_list.append({
        "id":
        assignment.id,
        "name":
        assignment.name,
        "points_possible":
        assignment.points_possible
        if hasattr(assignment, 'points_possible') else None,
      })

    # Sort alphabetically by name
    assignment_list.sort(key=lambda a: a['name'].lower())

    log.info(
      f"Found {len(assignment_list)} assignments for course {course_id}")
    return {"assignments": assignment_list}

  except Exception as e:
    log.error(f"Failed to fetch assignments: {e}", exc_info=True)
    raise HTTPException(status_code=500,
                        detail=f"Failed to fetch assignments: {str(e)}")


def get_canvas_interface(use_prod: bool = False):
  """
    Get CanvasInterface instance.
    Defaults to non-prod (dev) for safety.

    Args:
        use_prod: If True, use production Canvas; otherwise use dev/beta
    """
  from lms_interface.canvas_interface import CanvasInterface

  # Use existing CanvasInterface which handles ~/.env loading
  canvas_interface = CanvasInterface(prod=use_prod)

  return canvas_interface


@router.get("/courses/{course_id}")
async def get_course_info(course_id: int):
  """Fetch course information from Canvas"""
  try:
    canvas_interface = get_canvas_interface()

    # Use the existing get_course method
    course = canvas_interface.get_course(course_id)

    # Determine environment label
    # Check for beta/test in URL to detect dev, otherwise assume prod
    is_dev = "beta" in canvas_interface.canvas_url or "test" in canvas_interface.canvas_url
    env_label = "DEV" if is_dev else "PROD"

    return {
      "id": course_id,
      "name": course.name,
      "canvas_url": canvas_interface.canvas_url,
      "environment": env_label,  # Explicitly send environment
    }

  except ImportError as e:
    raise HTTPException(status_code=500,
                        detail=f"Canvas interface not available: {str(e)}")
  except Exception as e:
    raise HTTPException(status_code=404, detail=f"Course not found: {str(e)}")


@router.get("/courses/{course_id}/assignments/{assignment_id}")
async def get_assignment_info(course_id: int, assignment_id: int):
  """Fetch assignment information from Canvas"""
  import logging
  log = logging.getLogger(__name__)

  try:
    canvas_interface = get_canvas_interface()

    # Use the existing get_course method
    course = canvas_interface.get_course(course_id)
    log.info(
      f"Found course: {course.name} (URL: {canvas_interface.canvas_url})")

    # Use the existing get_assignment method from CanvasCourse
    assignment = course.get_assignment(assignment_id)
    log.info(f"Assignment lookup result: {assignment}")

    if not assignment:
      raise HTTPException(
        status_code=404,
        detail=f"Assignment {assignment_id} not found in course {course_id}")

    # Determine environment label
    # Check for beta/test in URL to detect dev, otherwise assume prod
    is_dev = "beta" in canvas_interface.canvas_url or "test" in canvas_interface.canvas_url
    env_label = "DEV" if is_dev else "PROD"

    return {
      "id": assignment_id,
      "name": assignment.name,
      "points_possible": assignment.points_possible,
      "canvas_url": canvas_interface.canvas_url,
      "environment": env_label,  # Explicitly send environment
    }

  except ImportError as e:
    raise HTTPException(status_code=500,
                        detail=f"Canvas interface not available: {str(e)}")
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=404,
                        detail=f"Error fetching assignment: {str(e)}")
