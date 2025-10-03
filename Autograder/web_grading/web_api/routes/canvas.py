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


def get_canvas_interface():
    """
    Get CanvasInterface instance.
    Defaults to non-prod (dev) for safety.
    """
    from lms_interface.canvas_interface import CanvasInterface

    # Check if we should use prod (must be explicitly set)
    use_prod = os.getenv("USE_PROD_CANVAS", "false").lower() == "true"

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

        return {
            "id": course_id,
            "name": course.name,
            "canvas_url": canvas_interface.canvas_url,  # Show which environment we're using
        }

    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Canvas interface not available: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Course not found: {str(e)}"
        )


@router.get("/courses/{course_id}/assignments/{assignment_id}")
async def get_assignment_info(course_id: int, assignment_id: int):
    """Fetch assignment information from Canvas"""
    try:
        canvas_interface = get_canvas_interface()

        # Use the existing get_course method
        course = canvas_interface.get_course(course_id)

        # Use the existing get_assignment method from CanvasCourse
        assignment = course.get_assignment(assignment_id)

        if not assignment:
            raise HTTPException(
                status_code=404,
                detail=f"Assignment {assignment_id} not found in course {course_id}"
            )

        return {
            "id": assignment_id,
            "name": assignment.name,
            "points_possible": assignment.points_possible,
            "canvas_url": canvas_interface.canvas_url,  # Show which environment we're using
        }

    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Canvas interface not available: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Error fetching assignment: {str(e)}"
        )
