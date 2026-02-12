from types import SimpleNamespace

from Autograder import grade_assignments


def test_execute_grading_returns_empty_list_for_no_assignments():
  args = SimpleNamespace(max_workers=None)
  assert grade_assignments.execute_grading([], args) == []


def test_grade_single_assignment_blocks_quiz_flow():
  result = grade_assignments.grade_single_assignment({
    "course": None,
    "course_name": "CST",
    "yaml_assignment": {
      "id": 1
    },
    "merged_assignment": {
      "type": "quiz",
      "kind": "QuizAssignment",
      "grader": "QuizGrader"
    },
    "args": SimpleNamespace(
      do_regrade=False, merge_only=False, limit=None, test=False),
    "push_grades": False,
  })

  assert result["success"] is False
  assert "disabled" in result["error"].lower()


def test_grade_single_assignment_blocks_exam_kind():
  result = grade_assignments.grade_single_assignment({
    "course": None,
    "course_name": "CST",
    "yaml_assignment": {
      "id": 2
    },
    "merged_assignment": {
      "type": "assignment",
      "kind": "Exam",
      "grader": "Manual"
    },
    "args": SimpleNamespace(
      do_regrade=False, merge_only=False, limit=None, test=False),
    "push_grades": False,
  })

  assert result["success"] is False
  assert "disabled" in result["error"].lower()
