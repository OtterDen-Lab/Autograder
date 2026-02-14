from Autograder import grade_assignments
from lms_interface.canvas_interface import CanvasInterface
from lms_interface.classes import Student, Feedback, Submission


def test_canvas_interface_resolve_student_name_id_only():
  interface = CanvasInterface(canvas_url="https://canvas.example.edu",
                              canvas_key="token",
                              privacy_mode="id_only")
  assert interface.resolve_student_name(12345, raw_name="Alice") == "Student 12345"


def test_canvas_interface_resolve_student_name_none_mode():
  interface = CanvasInterface(canvas_url="https://canvas.example.edu",
                              canvas_key="token",
                              privacy_mode="none")
  assert interface.resolve_student_name(12345, raw_name="Alice") == "Alice"


def test_canvas_interface_resolve_student_name_blind_is_stable():
  interface = CanvasInterface(canvas_url="https://canvas.example.edu",
                              canvas_key="token",
                              privacy_mode="blind")
  first = interface.resolve_student_name(12345, raw_name="Alice")
  second = interface.resolve_student_name(12345, raw_name="Alice")
  third = interface.resolve_student_name(67890, raw_name="Bob")

  assert first == second
  assert first.startswith("Anon ")
  assert third.startswith("Anon ")
  assert first != third


def test_canvas_interface_blind_mode_persists_labels_across_instances(tmp_path):
  map_path = tmp_path / "blind_map.json"
  first_interface = CanvasInterface(canvas_url="https://canvas.example.edu",
                                    canvas_key="token",
                                    privacy_mode="blind",
                                    blind_id_map_path=str(map_path))
  first_label = first_interface.resolve_student_name(12345, raw_name="Alice")
  assert first_label.startswith("Anon ")

  second_interface = CanvasInterface(canvas_url="https://canvas.example.edu",
                                     canvas_key="token",
                                     privacy_mode="blind",
                                     blind_id_map_path=str(map_path))
  second_label = second_interface.resolve_student_name(12345, raw_name="Alice")
  assert second_label == first_label


def test_format_submission_for_log_can_reveal_canvas_id():
  submission = Submission(student=Student(name="Anon 0001", user_id=12345, _inner=None),
                          status=Submission.Status.UNGRADED)
  submission.feedback = Feedback(percentage_score=100.0, comments="ok")

  hidden = grade_assignments.format_submission_for_log(submission,
                                                       reveal_identity=False)
  revealed = grade_assignments.format_submission_for_log(submission,
                                                         reveal_identity=True)

  assert "canvas_user_id" not in hidden
  assert "canvas_user_id=12345" in revealed
