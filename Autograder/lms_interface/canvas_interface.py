#!env python
from __future__ import annotations

import queue
import tempfile
import time
import typing
from datetime import datetime, timezone
from typing import List, Optional

import canvasapi
import canvasapi.course
import canvasapi.quiz
import canvasapi.assignment
import canvasapi.submission
import canvasapi.exceptions
import dotenv, os
import requests
from canvasapi.util import combine_kwargs

try:
  from urllib3.util.retry import Retry  # urllib3 v2
except Exception:
  from urllib3.util import Retry        # urllib3 v1 fallback

import os
import dotenv

from .classes import LMSWrapper, Student, Submission, Submission__Canvas, FileSubmission__Canvas, TextSubmission__Canvas, QuizSubmission

import logging

log = logging.getLogger(__name__)

QUESTION_VARIATIONS_TO_TRY = 1000
NUM_WORKERS = 4


class CanvasInterface:
  def __init__(self, *, prod=False):
    dotenv.load_dotenv(os.path.join(os.path.expanduser("~"), ".env"))

    self.prod = prod
    if self.prod:
      log.warning("Using canvas PROD!")
      self.canvas_url = os.environ.get("CANVAS_API_URL_prod")
      self.canvas_key = os.environ.get("CANVAS_API_KEY_prod")
    else:
      log.info("Using canvas DEV")
      self.canvas_url = os.environ.get("CANVAS_API_URL")
      self.canvas_key = os.environ.get("CANVAS_API_KEY")

    # Monkeypatch BEFORE constructing Canvas so all children use RobustRequester.
    # cap_req.Requester = RobustRequester
    # cap_canvas.Requester = RobustRequester
    self.canvas = canvasapi.Canvas(self.canvas_url, self.canvas_key)
    
  def get_course(self, course_id: int) -> CanvasCourse:
    return CanvasCourse(
      canvas_interface = self,
      canvasapi_course = self.canvas.get_course(course_id)
    )


class CanvasCourse(LMSWrapper):
  def __init__(self, *args, canvas_interface : CanvasInterface, canvasapi_course : canvasapi.course.Course, **kwargs):
    self.canvas_interface = canvas_interface
    self.course = canvasapi_course
    super().__init__(_inner=self.course)
  
  def create_assignment_group(self, name="dev", delete_existing=False) -> canvasapi.course.AssignmentGroup:
    for assignment_group in self.course.get_assignment_groups():
      if assignment_group.name == name:
        if delete_existing:
          assignment_group.delete()
          break
        log.info("Found group existing, returning")
        return assignment_group
    assignment_group = self.course.create_assignment_group(
      name=name,
      group_weight=0.0,
      position=0,
    )
    return assignment_group
  
  def add_quiz(
      self,
      assignment_group: canvasapi.course.AssignmentGroup,
      title = None,
      *,
      is_practice=False,
      description=None
  ):
    if title is None:
      title = f"New Quiz {datetime.now().strftime('%m/%d/%y %H:%M:%S.%f')}"

    if description is None:
      description = """
        This quiz is aimed to help you practice skills.
        Please take it as many times as necessary to get full marks!
        Please note that although the answers section may be a bit lengthy,
        below them is often an in-depth explanation on solving the problem!
      """

    q = self.course.create_quiz(quiz={
      "title": title,
      "hide_results" : None,
      "show_correct_answers": True,
      "scoring_policy": "keep_highest",
      "allowed_attempts": -1,
      "shuffle_answers": True,
      "assignment_group_id": assignment_group.id,
      "quiz_type" : "assignment" if not is_practice else "practice_quiz",
      "description": description
    })
    return q

  def push_quiz_to_canvas(
      self,
      quiz: Quiz,
      num_variations: int,
      title: typing.Optional[str] = None,
      is_practice = False,
      assignment_group: typing.Optional[canvasapi.course.AssignmentGroup] = None
  ):
    if assignment_group is None:
      assignment_group = self.create_assignment_group()
    canvas_quiz = self.add_quiz(assignment_group, title, is_practice=is_practice, description=quiz.description)
    
    total_questions = len(quiz.questions)
    total_variations_created = 0
    log.info(f"Starting to push quiz '{title or canvas_quiz.title}' with {total_questions} questions to Canvas")
    log.info(f"Target: {num_variations} variations per question")
    
    all_variations = set() # Track all variations so we can ensure we aren't uploading duplicates
    questions_to_upload = queue.Queue() # Make a queue of questions to upload so we can do so in the background
    
    # Generate all quiz questions
    for question_i, question in enumerate(quiz):
      log.info(f"Processing question {question_i + 1}/{total_questions}: '{question.name}'")
  
      group : canvasapi.quiz.QuizGroup = canvas_quiz.create_question_group([
        {
          "name": f"{question.name}",
          "pick_count": 1,
          "question_points": question.points_value
        }
      ])
      
      # Track all variations across every question, in case we have duplicate questions
      variation_count = 0
      for attempt_number in range(QUESTION_VARIATIONS_TO_TRY):

        # Get the question in a format that is ready for canvas (e.g. json)
        # Use large gaps between base seeds to avoid overlap with backoff attempts
        # Each variation gets seeds: base_seed, base_seed+1, base_seed+2, ... for backoffs
        base_seed = attempt_number * 1000
        question_for_canvas = question.get__canvas(self.course, canvas_quiz, rng_seed=base_seed)

        question_fingerprint = question_for_canvas["question_text"]
        try:
          question_fingerprint += ''.join([
            '|'.join([
              f"{k}:{a[k]}" for k in sorted(a.keys())
            ])
            for a in question_for_canvas["answers"]
          ])
        except TypeError as e:
          log.error(e)
          log.warning("Continuing anyway")

        # if it is in the variations that we have already seen then skip ahead, else track
        if question_fingerprint in all_variations:
          continue
        all_variations.add(question_fingerprint)
        
        # Push question to canvas
        log.info(f"Creating #{question_i} ({question.name}) {variation_count + 1} / {num_variations} for canvas.")
        
        # Set group ID to add it to the question group
        question_for_canvas["quiz_group_id"] = group.id

        questions_to_upload.put(question_for_canvas)
        total_variations_created += 1
      
        # Update and check variations already seen
        variation_count += 1
        if variation_count >= num_variations:
          break
        if variation_count >= question.possible_variations:
          break
      
      log.info(f"Completed question '{question.name}': {variation_count} variations created")

    # Upload questions
    num_questions_to_upload = questions_to_upload.qsize()
    while not questions_to_upload.empty():
      q_to_upload = questions_to_upload.get()
      log.info(f"Uploading {num_questions_to_upload-questions_to_upload.qsize()} / {num_questions_to_upload} to canvas!")
      try:
        canvas_quiz.create_question(question=q_to_upload)
      except canvasapi.exceptions.CanvasException as e:
        log.warning("Encountered Canvas error.")
        log.warning(e)
        questions_to_upload.put(q_to_upload)
        log.warning("Sleeping for 1s...")
        time.sleep(1)
        continue
    
    log.info(f"Quiz upload completed! Total variations created: {total_variations_created}")
    log.info(f"Canvas quiz URL: {canvas_quiz.html_url}")
  
  def get_assignment(self, assignment_id : int) -> Optional[CanvasAssignment]:
    try:
      return CanvasAssignment(
        canvasapi_interface=self.canvas_interface,
        canvasapi_course=self,
        canvasapi_assignment=self.course.get_assignment(assignment_id)
      )
    except canvasapi.exceptions.ResourceDoesNotExist:
      log.error(f"Assignment {assignment_id} not found in course \"{self.name}\"")
      return None
    
  def get_assignments(self, **kwargs) -> List[CanvasAssignment]:
    assignments : List[CanvasAssignment] = []
    for canvasapi_assignment in self.course.get_assignments(**kwargs):
      assignments.append(
        CanvasAssignment(
          canvasapi_interface=self.canvas_interface,
          canvasapi_course=self,
          canvasapi_assignment=canvasapi_assignment
        )
      )
    
    assignments = self.course.get_assignments(**kwargs)
    return assignments
  
  def get_username(self, user_id: int):
    return self.course.get_user(user_id).name
  
  def get_students(self) -> List[Student]:
    return [Student(s.name, s.id, s) for s in self.course.get_users(enrollment_type=["student"])]

  def get_quiz(self, quiz_id: int) -> Optional[CanvasQuiz]:
    """Get a specific quiz by ID"""
    try:
      return CanvasQuiz(
        canvas_interface=self.canvas_interface,
        canvasapi_course=self,
        canvasapi_quiz=self.course.get_quiz(quiz_id)
      )
    except canvasapi.exceptions.ResourceDoesNotExist:
      log.error(f"Quiz {quiz_id} not found in course \"{self.name}\"")
      return None

  def get_quizzes(self, **kwargs) -> List[CanvasQuiz]:
    """Get all quizzes in the course"""
    quizzes: List[CanvasQuiz] = []
    for canvasapi_quiz in self.course.get_quizzes(**kwargs):
      quizzes.append(
        CanvasQuiz(
          canvas_interface=self.canvas_interface,
          canvasapi_course=self,
          canvasapi_quiz=canvasapi_quiz
        )
      )
    return quizzes


class CanvasAssignment(LMSWrapper):
  def __init__(self, *args, canvasapi_interface: CanvasInterface, canvasapi_course : CanvasCourse, canvasapi_assignment: canvasapi.assignment.Assignment, **kwargs):
    self.canvas_interface = canvasapi_interface
    self.canvas_course = canvasapi_course
    self.assignment = canvasapi_assignment
    super().__init__(_inner=canvasapi_assignment)
  
  def push_feedback(self, user_id, score: float, comments: str, attachments=None, keep_previous_best=True, clobber_feedback=False):
    log.debug(f"Adding feedback for {user_id}")
    if attachments is None:
      attachments = []
    
    # Get the previous score to check to see if we should reuse it
    try:
      submission = self.assignment.get_submission(user_id)
      if keep_previous_best and score is not None and submission.score is not None and submission.score > score:
        log.warning(f"Current score ({submission.score}) higher than new score ({score}).  Going to use previous score.")
        score = submission.score
    except requests.exceptions.ConnectionError as e:
      log.warning(f"No previous submission found for {user_id}")
    
    # Update the assignment
    # Note: the bulk_update will create a submission if none exists
    try:
      self.assignment.submissions_bulk_update(
        grade_data={
          'submission[posted_grade]' : score
        },
        student_ids=[user_id]
      )
      
      submission = self.assignment.get_submission(user_id)
    except requests.exceptions.ConnectionError as e:
      log.error(e)
      log.debug(f"Failed on user_id = {user_id})")
      log.debug(f"username: {self.canvas_course.get_user(user_id)}")
      return
    
    # Push feedback to canvas
    submission.edit(
      submission={
        'posted_grade':score,
      },
    )
    
    # If we should overwrite previous comments then remove all the previous submissions
    if clobber_feedback:
      log.debug("Clobbering...")
      # todo: clobbering should probably be moved up or made into a different function for cleanliness.
      for comment in submission.submission_comments:
        comment_id = comment['id']
        
        # Construct the URL to delete the comment
        api_path = f"/api/v1/courses/{self.canvas_course.course.id}/assignments/{self.assignment.id}/submissions/{user_id}/comments/{comment_id}"
        response = self.canvas_interface.canvas._Canvas__requester.request("DELETE", api_path)
        if response.status_code == 200:
          log.info(f"Deleted comment {comment_id}")
        else:
          log.warning(f"Failed to delete comment {comment_id}: {response.json()}")
    
    def upload_buffer_as_file(buffer: bytes, name: str):
      suffix = os.path.splitext(name)[1]  # keep extension if needed
      with tempfile.NamedTemporaryFile(mode="wb", delete=False, dir=".", prefix="feedback_", suffix=suffix) as tmp:
        tmp.write(buffer)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_path = tmp.name  # str path
      
      try:
        submission.upload_comment(temp_path)  # ✅ PathLike | str
      finally:
        os.remove(temp_path)
    
    if len(comments) > 0:
      upload_buffer_as_file(comments.encode('utf-8'), "feedback.txt")
    
    for i, attachment_buffer in enumerate(attachments):
      upload_buffer_as_file(attachment_buffer.read(), attachment_buffer.name)
  
  def get_submissions(self, only_include_most_recent: bool = True, **kwargs) -> List[Submission]:
    """
    Gets submission objects (in this case Submission__Canvas objects) that have students and potentially attachments
    :param only_include_most_recent: Include only the most recent submission
    :param kwargs:
    :return:
    """
    
    if "limit" in kwargs and kwargs["limit"] is not None:
      limit = kwargs["limit"]
    else:
      limit = 1_000_000 # magically large number
    
    test_only = kwargs.get("test", False)
    
    submissions: List[Submission] = []
    
    # Get all submissions and their history (which is necessary for attachments when students can resubmit)
    for student_index, canvaspai_submission in enumerate(self.assignment.get_submissions(include='submission_history', **kwargs)):
      
      # Get the student object for the submission
      student = Student(
        self.canvas_course.get_username(canvaspai_submission.user_id),
        user_id=canvaspai_submission.user_id,
        _inner=self.canvas_course.get_user(canvaspai_submission.user_id)
      )
      
      if test_only and not "Test Student" in student.name:
        continue
      
      log.debug(f"Checking submissions for {student.name} ({len(canvaspai_submission.submission_history)} submissions)")
      
      # Walk through submissions in the reverse order, so we'll default to grabbing the most recent submission first
      # This is important when we are going to be only including most recent
      for student_submission_index, student_submission in (
          reversed(list(enumerate(canvaspai_submission.submission_history)))):
        log.debug(f"Submission: {student_submission['workflow_state']} " +
                  (f"{student_submission['score']:0.2f}" if student_submission['score'] is not None else "None"))
        
        # Determine submission type based on content
        has_attachments = student_submission.get("attachments") is not None and len(student_submission.get("attachments", [])) > 0
        has_text_body = student_submission.get("body") is not None and student_submission.get("body").strip() != ""

        if has_text_body:
          # Text submission - create object-like structure from dict
          log.debug(f"Detected text submission for {student.name}")
          class SubmissionObject:
            def __init__(self, data):
              for key, value in data.items():
                setattr(self, key, value)

          submissions.append(
            TextSubmission__Canvas(
              student=student,
              status=Submission.Status.from_string(student_submission["workflow_state"], student_submission['score']),
              canvas_submission_data=SubmissionObject(student_submission),
              submission_index=student_submission_index
            )
          )
        elif has_attachments:
          # File submission
          log.debug(f"Detected file submission for {student.name}")
          submissions.append(
            FileSubmission__Canvas(
              student=student,
              status=Submission.Status.from_string(student_submission["workflow_state"], student_submission['score']),
              attachments=student_submission["attachments"],
              submission_index=student_submission_index
            )
          )
        else:
          # No submission content found
          log.debug(f"No submission content found for {student.name}")
          continue
        
        # Check if we should only include the most recent
        if only_include_most_recent: break
      
      # Check if we are limiting how many students we are checking
      if student_index >= (limit - 1): break
      
    # Reverse the submissions again so we are preserving temporal order.  This isn't necessary but makes me feel happy.
    submissions = list(reversed(submissions))
    return submissions
  
  def get_students(self):
    return self.canvas_course.get_students()


class CanvasQuiz(LMSWrapper):
  """Canvas quiz interface for handling quiz submissions and responses"""

  def __init__(self, *args, canvas_interface: CanvasInterface, canvasapi_course: CanvasCourse, canvasapi_quiz: canvasapi.quiz.Quiz, **kwargs):
    self.canvas_interface = canvas_interface
    self.canvas_course = canvasapi_course
    self.quiz = canvasapi_quiz
    super().__init__(_inner=canvasapi_quiz)

  def get_quiz_submissions(self, **kwargs) -> List[QuizSubmission]:
    """
    Get all quiz submissions with student responses
    :param kwargs: Additional parameters for filtering
    :return: List of QuizSubmission objects
    """
    test_only = kwargs.get("test", False)
    limit = kwargs.get("limit", 1_000_000)

    quiz_submissions: List[QuizSubmission] = []

    # Get all quiz submissions
    for student_index, canvasapi_quiz_submission in enumerate(self.quiz.get_submissions(**kwargs)):

      # Get the student object for the submission
      try:
        student = Student(
          self.canvas_course.get_username(canvasapi_quiz_submission.user_id),
          user_id=canvasapi_quiz_submission.user_id,
          _inner=self.canvas_course.get_user(canvasapi_quiz_submission.user_id)
        )
      except Exception as e:
        log.warning(f"Could not get student info for user_id {canvasapi_quiz_submission.user_id}: {e}")
        continue

      if test_only and "Test Student" not in student.name:
        continue

      log.debug(f"Processing quiz submission for {student.name}")

      # Get detailed submission responses
      try:
        submission_questions = canvasapi_quiz_submission.get_submission_questions()

        # Convert to our format: question_id -> response
        student_responses = {}
        quiz_questions = {}

        for question in submission_questions:
          question_id = question.id
          student_responses[question_id] = {
            'answer': question.answer,
            'correct': getattr(question, 'correct', None),
            'points': getattr(question, 'points', 0),
            'question_type': getattr(question, 'question_type', 'unknown')
          }

          # Store question metadata
          quiz_questions[question_id] = {
            'question_name': getattr(question, 'question_name', ''),
            'question_text': getattr(question, 'question_text', ''),
            'question_type': getattr(question, 'question_type', 'unknown'),
            'points_possible': getattr(question, 'points_possible', 0)
          }

        # Create QuizSubmission object
        quiz_submission = QuizSubmission(
          student=student,
          status=Submission.Status.from_string(canvasapi_quiz_submission.workflow_state, canvasapi_quiz_submission.percentage_score),
          quiz_submission_data=canvasapi_quiz_submission,
          student_responses=student_responses,
          quiz_questions=quiz_questions
        )

        quiz_submissions.append(quiz_submission)

      except Exception as e:
        log.error(f"Failed to get submission questions for {student.name}: {e}")
        continue

      # Check if we are limiting how many students we are checking
      if student_index >= (limit - 1):
        break

    return quiz_submissions

  def get_questions(self):
    """Get all quiz questions"""
    return self.quiz.get_questions()

  def push_feedback(self, user_id, score: float, comments: str, **kwargs):
    """
    Push feedback for a quiz submission
    Note: Quiz feedback mechanisms may be different from assignment feedback
    """
    # Quiz submissions typically don't support the same feedback mechanisms as assignments
    # This is a placeholder for quiz-specific feedback handling
    log.warning("Quiz feedback pushing not yet implemented")
    pass


class CanvasHelpers:
  @staticmethod
  def get_closed_assignments(interface: CanvasCourse) -> List[canvasapi.assignment.Assignment]:
    closed_assignments : List[canvasapi.assignment.Assignment] = []
    for assignment in interface.get_assignments(
      include=["all_dates"], 
      order_by="name"
    ):
      if not assignment.published:
        continue
      if assignment.lock_at is not None:
        # Then it's the easy case because there's no overrides
        if datetime.fromisoformat(assignment.lock_at) < datetime.now(timezone.utc):
          # Then the assignment is past due
          closed_assignments.append(assignment)
          continue
      elif assignment.all_dates is not None:
        
        # First we need to figure out what the latest time this assignment could be available is
        # todo: This could be done on a per-student basis
        last_lock_datetime = None
        for dates_dict in assignment.all_dates:
          if dates_dict["lock_at"] is not None:
            lock_datetime = datetime.fromisoformat(dates_dict["lock_at"])
            if (last_lock_datetime is None) or (lock_datetime >= last_lock_datetime):
              last_lock_datetime = lock_datetime
        
        # If we have found a valid lock time, and it's in the past then we lock
        if last_lock_datetime is not None and last_lock_datetime <= datetime.now(timezone.utc):
          closed_assignments.append(assignment)
          continue
          
      else:
        log.warning(f"Cannot find any lock dates for assignment {assignment.name}!")
    
    return closed_assignments
  
  @staticmethod
  def get_unsubmitted_submissions(interface: CanvasCourse, assignment: canvasapi.assignment.Assignment) -> List[canvasapi.submission.Submission]:
    submissions : List[canvasapi.submission.Submission] = list(filter(
      lambda s: s.submitted_at is None and s.percentage_score is None and not s.excused,
      assignment.get_submissions()
    ))
    return submissions
  
  @classmethod
  def clear_out_missing(cls, interface: CanvasCourse):
    assignments = cls.get_closed_assignments(interface)
    for assignment in assignments:
      missing_submissions = cls.get_unsubmitted_submissions(interface, assignment)
      if not missing_submissions:
        continue
      log.info(f"Assignment: ({assignment.quiz_id if hasattr(assignment, 'quiz_id') else assignment.id}) {assignment.name} {assignment.published}")
      for submission in missing_submissions:
        log.info(f"{submission.user_id} ({interface.get_username(submission.user_id)}) : {submission.workflow_state} : {submission.missing} : {submission.score} : {submission.grader_id} : {submission.graded_at}")
        submission.edit(submission={"late_policy_status" : "missing"})
      log.info("")
  
  @staticmethod
  def deprecate_assignment(canvas_course: CanvasCourse, assignment_id) -> List[canvasapi.assignment.Assignment]:
    
    log.debug(canvas_course.__dict__)
    
    # for assignment in canvas_course.get_assignments():
    #   print(assignment)
    
    canvas_assignment : CanvasAssignment = canvas_course.get_assignment(assignment_id=assignment_id)
    
    canvas_assignment.assignment.edit(
      assignment={
        "name": f"{canvas_assignment.assignment.name} (deprecated)",
        "due_at": f"{datetime.now(timezone.utc).isoformat()}",
        "lock_at": f"{datetime.now(timezone.utc).isoformat()}"
      }
    )
  
  @staticmethod
  def mark_future_assignments_as_ungraded(canvas_course: CanvasCourse):
    
    for assignment in canvas_course.get_assignments(
        include=["all_dates"],
        order_by="name"
    ):
      if assignment.unlock_at is not None:
        if datetime.fromisoformat(assignment.unlock_at) > datetime.now(timezone.utc):
          log.debug(assignment)
          for submission in assignment.get_submissions():
            submission.mark_unread()
          
    
