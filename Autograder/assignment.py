#!env python
from __future__ import annotations

import base64
import collections
import math
import pathlib
import random
import re
import shutil
import sys
import threading
from datetime import datetime
from typing import List, Tuple, Dict, Optional
import io
import abc
import fitz
import fuzzywuzzy.fuzz
import os

import pandas as pd

import Autograder.ai_helper as ai_helper
import Autograder.exceptions
from Autograder.registry import AssignmentRegistry
from Autograder.lms_interface.canvas_interface import CanvasCourse, CanvasAssignment
from Autograder.lms_interface.classes import Student, Submission, Feedback

import logging
import colorama

log = logging.getLogger(__name__)

# Constants
NAME_SIMILARITY_THRESHOLD = 95  # Percentage threshold for fuzzy name matching


class Assignment(abc.ABC):
  """
  Class to represent an assignment and act as an abstract base class for other classes.  Will be passed to a grader.
  Two functions need to be overriden for child classes:
  1. prepare : prepares files for grading by downloading, anonymizing, etc.
  2. finalize : combines parts of grading as necessary
  """

  def __init__(self, lms_assignment: CanvasAssignment, grading_root_dir, *args,
               **kwargs):
    self.lms_assignment = lms_assignment
    self.grading_root_dir = grading_root_dir
    self.submissions: List[Submission] = []
    self.original_dir = None
    self.canvas_points = kwargs.get(
      'canvas_points', None)  # Override for Canvas assignment points

  def __enter__(self) -> Assignment:
    """Enables use as a context manager (e.g. `with [Assignment]`) by managing working directory"""
    # todo: Enable use of anonymous temp directories

    # Only change working directory if we're in the main thread to avoid race conditions
    if threading.current_thread() == threading.main_thread():
      self.original_dir = os.getcwd()
      os.chdir(self.grading_root_dir)
    else:
      # In worker threads, don't change the working directory
      self.original_dir = None
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    """Enables use as a context manager (e.g. `with [Assignment]`) by managing working directory"""
    # Only restore working directory if we changed it
    if self.original_dir is not None:
      os.chdir(self.original_dir)

  @abc.abstractmethod
  def prepare(self, *args, **kwargs) -> None:
    """
    This function is intended to set up any directories or files as appropriate for grading.
    It should take in some sort of input and prepare Submissions to be passed to a grader object.
    :return: None
    """
    pass

  def finalize(self, *args, **kwargs) -> None:
    """
    This function is intended to finalize any grading.  This could be reloading the grading CSV and matching names,
    or could just be a noop.
    :param args:
    :param kwargs:
    :return:
    """

    # If we are only merging then we should exit right here
    if kwargs.get("merge_only", False):
      return

    log.debug("Pushing")
    for submission in self.submissions:

      # Handle record retention before pushing to LMS
      if kwargs.get("record_retention", False):
        self._save_feedback_record(submission.student,
                                   submission.feedback.comments,
                                   kwargs.get("records_dir"),
                                   self.lms_assignment.name)

      if kwargs.get("push", False):
        log.info(f"Pushing feedback for: {submission}")
        # Scale the score for Canvas submission
        scaled_score = self.scale_score_for_canvas(
          submission.feedback.percentage_score)
        self.lms_assignment.push_feedback(
          score=scaled_score,
          comments=submission.feedback.comments,
          attachments=submission.feedback.attachments,
          user_id=submission.student.user_id,
          keep_previous_best=True,
          clobber_feedback=False)

  def _save_feedback_record(self, student: Student, comments: str,
                            records_dir: str, assignment_name: str) -> None:
    """
    Save feedback to records directory for record retention.
    
    Args:
        student: Student object
        comments: Feedback comments to save
        records_dir: Directory path where records should be saved
        assignment_name: Name of the assignment
    """
    try:
      # Sanitize student name for filename (remove/replace unsafe characters)
      student_name = re.sub(r'[^\w\-_.]', '', student.name.replace(' ', '_'))

      # Sanitize assignment name for filename (remove/replace unsafe characters)
      assignment_safe = re.sub(r'[^\w\-_.]', '',
                               assignment_name.replace(' ', '_'))

      # Create timestamp
      timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

      # Ensure records directory exists
      if not os.path.exists(records_dir):
        os.makedirs(records_dir)
        log.info(f"Created records directory: {records_dir}")

      # Create filename: [assignment_name].[student_name].[timestamp].log
      filename = f"{assignment_safe}.{student_name}.{timestamp}.log"
      filepath = os.path.join(records_dir, filename)

      # Write feedback to file
      with open(filepath, 'w', encoding='utf-8') as f:
        f.write(comments)

      log.info(f"Saved feedback record: {filepath}")

    except Exception as e:
      log.error(
        f"Failed to save feedback record for student {student.name}: {e}")

  def scale_score_for_canvas(self, percentage_score: float) -> float:
    """
    Scale a percentage score to match Canvas assignment points.

    This method assumes ALL scores coming from graders are percentages (0-100+).
    It scales them to the Canvas assignment's point value.

    Prioritizes:
    1. Explicit canvas_points parameter from YAML config
    2. Canvas assignment's points_possible if available
    3. Percentage score as-is if no scaling info available (fallback)

    Args:
        percentage_score: The percentage score from the grader (0-100+, where 100 = full credit)

    Returns:
        Scaled score for Canvas submission in Canvas points
    """
    try:
      # Determine Canvas points to use (in priority order)
      canvas_points_possible = None

      # 1. Use explicit canvas_points parameter from YAML config
      if self.canvas_points is not None:
        canvas_points_possible = float(self.canvas_points)
        log.info(
          f"Using explicit canvas_points from config: {canvas_points_possible}"
        )

      # 2. Try to get points_possible from Canvas assignment
      elif hasattr(self.lms_assignment, 'points_possible'
                   ) and self.lms_assignment.points_possible is not None:
        canvas_points_possible = float(self.lms_assignment.points_possible)
        log.info(
          f"Using Canvas assignment points_possible: {canvas_points_possible}")

      # Scale percentage to Canvas points
      if canvas_points_possible is not None:
        # Convert percentage to decimal and multiply by Canvas points
        # Example: 101% on 80-point assignment = 1.01 * 80 = 80.8 points
        percentage_decimal = percentage_score / 100.0
        scaled_score = percentage_decimal * canvas_points_possible
        log.info(
          f"Scaled score {percentage_score:.2f}% to {scaled_score:.2f}/{canvas_points_possible} Canvas points"
        )
        return scaled_score
      else:
        # Fallback: no Canvas points info available, pass through percentage as-is
        log.info(
          f"Using percentage score as-is: {percentage_score:.2f} (no Canvas points info available)"
        )
        return percentage_score

    except Exception as e:
      log.warning(
        f"Failed to scale score for Canvas: {e}. Using percentage score as-is: {percentage_score}"
      )
      return percentage_score


@AssignmentRegistry.register("ProgrammingAssignment")
class Assignment__ProgrammingAssignment(Assignment):
  """
  Assignment for programming assignment grading, where prepare will download files and finalize will upload feedback.
  Will hopefully be run automatically.
  """
  
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)

  def prepare(self,
              *args,
              limit=None,
              do_regrade=False,
              only_include_latest=True,
              **kwargs):

    # Steps:
    #  1. Get the submissions
    #  2. Filter out submissions we don't want
    #  3. possibly download proactively
    log.info(
      f"Preparing assignment with do_regrade={do_regrade}, limit={limit}")
    self.submissions = self.lms_assignment.get_submissions(
      limit=(None if not do_regrade else limit), **kwargs)
    log.info(f"Retrieved {len(self.submissions)} total submissions from LMS")

    if not do_regrade:
      ungraded_before = len(self.submissions)
      self.submissions = list(
        filter(lambda s: s.status == Submission.Status.UNGRADED,
               self.submissions))
      log.info(
        f"Filtered to {len(self.submissions)} ungraded submissions (was {ungraded_before})"
      )
    else:
      log.info("Regrade mode: processing all submissions regardless of status")

    # If a student changed the filename, try to fix it automatically.
    # for submission in self.submissions:
    #   for f in submission.files:
    #     if f.name not in self.allowed_filenames:
    #       # Then we'll need to try to match it.
    #       new_name = max(self.allowed_filenames,
    #                      key=(lambda s: fuzzywuzzy.fuzz.ratio(s, f.name)))
    #
    #       # If we have a suffix, use it to rename -- otherwise take the original final name
    #       if pathlib.Path(new_name).suffix == "":
    #         new_name = f"{new_name}{pathlib.Path(f.name).suffix}"
    #       else:
    #         new_name = f"{new_name}"
    #
    #       log.info(f"Renaming {f.name} to {new_name}")
    #       f.name = new_name

    log.info(f"Total students to grade: {len(self.submissions)}")
    if limit is not None:
      log.warning(f"Limiting to {limit} students")
      self.submissions = self.submissions[:limit]
    for i, submission in enumerate(self.submissions):
      try:
        log.debug(
          f"{i+1 : 0{math.ceil(math.log10(len(self.submissions)))}} : {submission.student.name} -> files: {[f.name for f in submission.files]}"
        )
      except AttributeError as e:
        log.warning(
          f"Failed to log submission info for {submission.student.name}: missing files attribute"
        )
        log.debug(f"AttributeError details: {e}")
      except Exception as e:
        log.error(
          f"Unexpected error logging submission info for {submission.student.name}: {e}"
        )

  def finalize(self, *args, **kwargs):
    super().finalize(*args, **kwargs)


@AssignmentRegistry.register("Exam")
class Assignment__Exam(Assignment):
  # Default name detection rectangle coordinates (pixels)
  NAME_RECT = {"x": 350, "y": 0, "width": 250, "height": 150}

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)

    self.fitz_name_rect = fitz.Rect([
      self.NAME_RECT["x"],
      self.NAME_RECT["y"],
      self.NAME_RECT["x"] + self.NAME_RECT["width"],
      self.NAME_RECT["y"] + self.NAME_RECT["height"],
    ])

  class Submission__pdf(Submission):

    def __init__(self,
                 document_id,
                 *args,
                 student=None,
                 approximate_name=None,
                 feedback: Optional[Feedback] = None,
                 question_scores=None,
                 **kwargs):
      super().__init__(*args, **kwargs)
      self.document_id = document_id
      self.approximate_name = approximate_name
      if student is not None:
        self.approximate_name = student.name
        Submission.student.fset(self, student)
      self.feedback: Optional[Feedback] = feedback
      self.question_scores: Dict[int, float] = question_scores

    @Submission.student.setter
    def student(self, student):
      new_name_ratio = fuzzywuzzy.fuzz.ratio(student.name,
                                             self.approximate_name)
      old_name_ratio = 0 if self.student is None else fuzzywuzzy.fuzz.ratio(
        self.student.name, self.approximate_name)
      log.info(
        f'Setting student to "{student}" ({new_name_ratio}%) ({self.approximate_name})'
      )
      if (fuzzywuzzy.fuzz.ratio(student.name, self.approximate_name) /
          100.0) <= NAME_SIMILARITY_THRESHOLD:
        log.warning(colorama.Back.RED + colorama.Fore.LIGHTGREEN_EX +
                    colorama.Style.BRIGHT + "Similarity below threshold" +
                    colorama.Style.RESET_ALL)

      if new_name_ratio < old_name_ratio:
        log.warning(colorama.Back.RED + colorama.Fore.LIGHTGREEN_EX +
                    colorama.Style.BRIGHT + "New name worse than old name" +
                    colorama.Style.RESET_ALL)

      # Call the parent class's student setter explicitly
      Submission.student.fset(self, student)

  def prepare(self, input_directory, limit=None, *args, **kwargs):

    # Get students from canvas to try to match
    canvas_students: List[Student] = self.lms_assignment.get_students()
    unmatched_canvas_students: List[Student] = canvas_students
    log.debug(canvas_students)

    # Read in all the PDFs
    input_pdfs = [
      os.path.join(input_directory, f) for f in os.listdir(input_directory)
      if f.endswith(".pdf")
    ]
    log.debug(f"input_pdfs: {input_pdfs}")

    # Clean and create output folders
    for path_name in ["01-shuffled", "02-redacted"]:
      if os.path.exists(path_name):
        shutil.rmtree(path_name)
      os.mkdir(path_name)

    # Shuffle inputs
    random.shuffle(input_pdfs)

    # Prepare page ranges
    num_pages_in_pdf = fitz.open(input_pdfs[0]).page_count
    pages_to_merge = [
      num for start, end in kwargs.get("pages_to_merge", [])
      for num in range(start, end + 1)
    ]
    page_ranges = [(p, p) for p in range(num_pages_in_pdf)
                   if p not in pages_to_merge]
    page_ranges.extend([tuple(r) for r in kwargs.get("pages_to_merge", [])])
    page_ranges.sort()

    # Pre-allocate page mappings
    # It feels weird to pre-allocate, but it makes it clearer than doing it in flow I think
    pdfs_to_process = input_pdfs[:(
      limit if limit is not None else len(input_pdfs))]
    num_users = len(pdfs_to_process)
    num_pages_to_expect = len(page_ranges)
    page_mappings_by_user = collections.defaultdict(list)

    # For each page, shuffle the order so we can handle them in different orders
    for page in range(num_pages_to_expect):
      for user_id, random_page_id in enumerate(
          random.sample(range(num_users), k=num_users)):
        page_mappings_by_user[user_id].append(random_page_id)

    # Walk through student submissions, shuffle and redact, and get approximate name
    assignment_submissions: List[Assignment__Exam.Submission__pdf] = []
    for document_id, pdf_filepath in enumerate(pdfs_to_process):
      log.debug(f"Processing {document_id+1}th document: {pdf_filepath}")

      approximate_student_name = self.get_approximate_student_name(
        path_to_pdf=pdf_filepath,
        use_ai=kwargs.get("use_ai", True),
        all_student_names=[s.name for s in unmatched_canvas_students])
      log.debug(f"Suggested name: {approximate_student_name}")

      # Find best match of the unmatched canvas
      (score, best_match) = max(
        ((fuzzywuzzy.fuzz.ratio(s.name, approximate_student_name), s)
         for s in unmatched_canvas_students),
        key=lambda x: x[0])
      if score > NAME_SIMILARITY_THRESHOLD:
        submission = Assignment__Exam.Submission__pdf(document_id,
                                                      student=best_match)
        unmatched_canvas_students.remove(best_match)
      else:
        log.warning(
          f"Rejecting proposed match for \"{approximate_student_name}\": \"{best_match.name}\" ({score})"
        )
        submission = Assignment__Exam.Submission__pdf(
          document_id, approximate_name=approximate_student_name)

      # Add in the page numbers
      submission.set_extra({"document_id": document_id})
      submission.set_extra({
        f"P{page_number}": page
        for page_number, page in enumerate(page_mappings_by_user[document_id])
      })
      submission.set_extra(
        {"page_mappings": page_mappings_by_user[document_id]})

      # Add to submissions
      assignment_submissions.append(submission)

      # Save aside a copy that's been shuffled for later reference and easy confirmation
      shuffled_document = fitz.open(pdf_filepath)
      shuffled_document.save(
        os.path.join("01-shuffled", f"{document_id:03}.pdf"))

      # Break up each submission into pages
      page_docs: List[fitz.Document] = self.redact_and_split(
        pdf_filepath, page_ranges=page_ranges)
      for page_number, page in enumerate(page_docs):

        # Determine the output directory
        page_directory = os.path.join("02-redacted", f"{page_number:03}")

        # Make the output directroy if it doesn't exist
        if not os.path.exists(page_directory): os.mkdir(page_directory)

        # Save the page to the appropriate directory, with the number connected to it.
        try:
          page.save(
            os.path.join(
              page_directory,
              f"{page_mappings_by_user[document_id][page_number]:03}.pdf"))
          page.close()
        except IndexError:
          log.warning(f"No page {page_number} found for {document_id}")

    log.debug(f"assignment_submissions: {assignment_submissions}")

    self.submissions = assignment_submissions

    return

  def finalize(self, *args, **kwargs):
    log.debug("Finalizing grades")

    shutil.rmtree("03-finalized", ignore_errors=True)
    os.mkdir("03-finalized")

    for submission in self.submissions:
      log.debug(submission.__dict__)
      graded_exam = self.merge_pages(
        "02-redacted",
        submission.extra_info.get('page_mappings', []),
        output_path=os.path.join(
          "03-finalized",
          f"{int(submission.extra_info['document_id']):03}.pdf"))
      graded_exam.name = f"exam.pdf"
      submission.feedback.attachments.append(graded_exam)
      pass

    super().finalize(*args, **kwargs)

  @staticmethod
  def match_students_to_submissions(
    students: List[Student], submissions: List[Submission__pdf]
  ) -> Tuple[List[Submission__pdf], List[Submission__pdf], List[Student],
             List[Student]]:
    # Modified from https://chatgpt.com/share/6743c2aa-477c-8001-9eb6-e5800c3f44da
    # todo: this function has become a mess of what it's doing and returning.
    submissions_w_names: List[Assignment__Exam.Submission__pdf] = []
    submissions_wo_names: List[Assignment__Exam.Submission__pdf] = []
    matched_students: List[Student] = []
    unmatched_students: List[Student] = []

    while students and submissions:
      # Find the pair with the maximum comparison value
      best_pair: Tuple[Assignment__Exam.Submission__pdf, Student] | None = None
      best_value = float('-inf')

      # todo: update this to go through by using itertools, so we can make sure that the 2nd best is significantly worse than the best

      # Go through all the submissions and compare for best match
      for submission in submissions:
        for student in students:
          log.debug(
            f"submission.approximate_name: {submission.approximate_name}")
          log.debug(f"student.name: {student.name}\n")
          value = fuzzywuzzy.fuzz.ratio(submission.approximate_name,
                                        student.name)
          if value > best_value:
            best_value = value
            best_pair = (submission, student)

      # Once we've figured out the best current match, assign the Student to the submission, or add it to the unmatched list.
      if (best_value / 100.0) > NAME_SIMILARITY_THRESHOLD:
        best_pair[0].student = best_pair[1]
        submissions_w_names.append(best_pair[0])
        matched_students.append(best_pair[1])
      else:
        submissions_wo_names.append(best_pair[0])
        unmatched_students.append(best_pair[1])
        log.warning("Threshold not met, skipping")

      # Remove the matches, even if it wasn't the best match
      submissions.remove(best_pair[0])
      students.remove(best_pair[1])
    try:
      log.debug(
        f"Matched {100*(len(submissions_w_names) / len(submissions_w_names + submissions_wo_names)):0.2f}% of submissions"
      )
    except ZeroDivisionError:
      log.warning("No possible submissions to match passed in")
    return submissions_w_names, submissions_wo_names, matched_students, unmatched_students

  def redact_and_split(self,
                       path_to_pdf: str,
                       page_ranges: Optional[List[Tuple[int, int]]] = None,
                       *args,
                       **kwargs) -> List[fitz.Document]:
    pdf_document = fitz.open(path_to_pdf)

    # First, we redact the first page
    pdf_document[0].draw_rect(self.fitz_name_rect,
                              color=(0, 0, 0),
                              fill=(0, 0, 0))

    # Next, we break the PDF up into individual pages:
    pdf_pages = []

    # If no ranges are specified, simply make groups of single pages
    if page_ranges is None:
      num_pages_per_group = 3
      num_total_pages = len(pdf_document)
      page_ranges = [
        (start, min(start + num_pages_per_group - 1, num_total_pages))
        for start in range(0, num_total_pages + 1, num_pages_per_group)
      ]
    log.debug(f"page_ranges: {page_ranges}")

    # Loop through all pages
    for (start_page, end_page) in page_ranges:
      # Create a new document in memory
      single_page_pdf = fitz.open()

      # Insert the current page into the new document
      single_page_pdf.insert_pdf(pdf_document,
                                 from_page=start_page,
                                 to_page=end_page)

      # Append the single-page document to the list
      pdf_pages.append(single_page_pdf)

    return pdf_pages

  def get_approximate_student_name(self,
                                   path_to_pdf,
                                   use_ai=True,
                                   all_student_names=None):

    if use_ai:
      document = fitz.open(path_to_pdf)
      page = document[0]
      pix = page.get_pixmap(clip=list(self.fitz_name_rect))
      image_bytes = pix.tobytes("png")
      base64_str = base64.b64encode(image_bytes).decode("utf-8")
      document.close()

      query_string = "What name is written in this picture?  Please respond with only the name."
      if all_student_names is not None:
        query_string += "Some possible names are listed below, but use them as a guide rather than definitive list."
        query_string += "\n - ".join(sorted(all_student_names))
      response = ai_helper.AI_Helper__Anthropic().query_ai(query_string,
                                                           attachments=[
                                                             ("png",
                                                              base64_str)
                                                           ])
      return response
    else:
      return None

  @classmethod
  def merge_pages(cls,
                  input_directory,
                  page_mappings,
                  output_path: Optional[str] = None) -> io.BytesIO:
    exam_pdf = fitz.open()

    for page_number, page_map in enumerate(page_mappings):
      pdf_path = os.path.join(input_directory, f"{page_number:03}",
                              f"{page_map:03}.pdf")
      try:
        exam_pdf.insert_pdf(fitz.open(pdf_path))
      except RuntimeError as e:
        log.error("Page error")
        log.error(e)
        continue

    if output_path is not None:
      exam_pdf.save(output_path)

    output_bytes = io.BytesIO()
    exam_pdf.save(output_bytes)
    output_bytes.seek(0)
    return output_bytes

  def check_student_names(self,
                          submissions: List[Submission__pdf],
                          threshold=0.95):

    id_width = max(map(lambda s: len(str(s.student.user_id)), submissions))
    local_width = max(map(lambda s: len(s.student.name), submissions))

    comparisons = []
    log.debug("Checking user IDs")
    for submission in submissions:
      sys.stderr.write('.')
      sys.stderr.flush()
      # canvas_name = self.canvas_course.get_user(int(user_id)).name
      ratio = (fuzzywuzzy.fuzz.ratio(submission.approximate_name,
                                     submission.student.name) / 100.0)
      comparisons.append(
        (ratio, submission.student.user_id, submission.approximate_name,
         submission.student.name))
    sys.stderr.write('\n')

    for (ratio, user_id, student_name, canvas_name) in sorted(comparisons):
      compare_str = f"{user_id:{id_width}} : {100*ratio:3}% : {student_name:{local_width}} ?? {canvas_name}"
      if (fuzzywuzzy.fuzz.ratio(student_name, canvas_name) /
          100.0) <= threshold:
        compare_str = colorama.Back.RED + colorama.Fore.LIGHTGREEN_EX + colorama.Style.BRIGHT + compare_str + colorama.Style.RESET_ALL

      log.debug(compare_str)

  @staticmethod
  def generate_feedback_comments(df_row: pd.DataFrame):
    total_score = df_row["total"]
    by_question_scores = {}
    for key in df_row.keys():
      if key.startswith("Q"):
        by_question_scores[int(key.replace('Q', ''))] = df_row[key]

    feedback_comments_lines = []
    for key in sorted(by_question_scores.keys()):
      if by_question_scores[key] == "-":
        feedback_comments_lines.extend([
          f"Q{key:<{1+int(math.log10(len(by_question_scores)))}} : 0 (unanswered)"
        ])
      else:
        feedback_comments_lines.extend([
          f"Q{key:<{1+int(math.log10(len(by_question_scores)))}} : {int(by_question_scores[key])}"
        ])
    feedback_comments_lines.extend([f"Total: {total_score} points"])

    return '\n'.join(feedback_comments_lines)


@AssignmentRegistry.register("ExamCST231")
class Assignment__JoshExam(Assignment__Exam):
  # CST231-specific name detection rectangle coordinates (pixels)
  NAME_RECT = {"x": 210, "y": 150, "width": 350, "height": 100}


@AssignmentRegistry.register("TextAssignment")
class Assignment_TextAssignment(Assignment):
  """
  Assignment for text-based learning log submissions.
  Handles Canvas text submissions where students submit reflective writing.
  """

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.submission_data = []

  def prepare(self, limit=None, do_regrade=False, test=False, **kwargs):
    """
    Prepare text submissions by fetching them from Canvas.

    Args:
        limit: Maximum number of submissions to process
        do_regrade: Whether to regrade existing submissions
        test: Whether to only include test student submissions
        **kwargs: Additional arguments including:
                  - grade_after_lock_date: If True, skip preparation if assignment is not locked
    """
    log.info(
      f"Preparing text assignment with do_regrade={do_regrade}, limit={limit}, test={test}"
    )

    # Check if we should wait for lock date before grading (BEFORE any Canvas API calls)
    grade_after_lock_date = kwargs.get('grade_after_lock_date', False)
    if grade_after_lock_date:
      if not self._is_assignment_locked():
        log.info(
          "Assignment is not ready for grading - skipping preparation and all Canvas API calls"
        )
        # Set empty submissions list to skip grading
        self.submissions = []
        self.submission_data = []
        return

    # Get submissions from Canvas
    self.submissions = self.lms_assignment.get_submissions(
      limit=(None if not do_regrade else limit), test=test, **kwargs)
    log.info(f"Retrieved {len(self.submissions)} total submissions from LMS")

    # Filter for ungraded submissions if not regrading
    if not do_regrade:
      ungraded_before = len(self.submissions)
      self.submissions = list(
        filter(lambda s: s.status == Submission.Status.UNGRADED,
               self.submissions))
      log.info(
        f"Filtered to {len(self.submissions)} ungraded submissions (was {ungraded_before})"
      )
    else:
      log.info("Regrade mode: processing all submissions regardless of status")

    # Apply limit if specified
    if limit is not None:
      log.warning(f"Limiting to {limit} students")
      self.submissions = self.submissions[:limit]

    # Process and structure the submission data
    self.submission_data = []
    for i, submission in enumerate(self.submissions):
      # Extract text content from submission
      if hasattr(submission, 'submission_text'):
        # If it's a list, join without extra spaces (newlines preserve natural word boundaries)
        if isinstance(submission.submission_text, list):
          submission_text = '\n'.join(submission.submission_text)
        else:
          submission_text = str(submission.submission_text)
      else:
        submission_text = ""

      word_count = len(submission_text.split()) if submission_text else 0

      self.submission_data.append({
        'student_id': submission.student.user_id,
        'student_name': submission.student.name,
        'text': submission_text,
        'word_count': word_count,
        'submission_obj': submission  # Keep reference to original submission
      })

      log.debug(f"{i+1} : {submission.student.name} -> {word_count} words")

    log.info(
      f"Prepared {len(self.submission_data)} text submissions for grading")

  def _is_assignment_locked(self) -> bool:
    """
    Check if the assignment is fully locked (past both due date and lock date).

    For learning logs, we want to ensure:
    1. Students have submitted their work (due_at passed)
    2. No more submissions can be made (lock_at passed)

    Returns:
        True if assignment is fully locked (past both due_at and lock_at), False otherwise
    """
    from datetime import datetime, timezone
    import dateutil.parser

    try:
      # Get current time in UTC
      now = datetime.now(timezone.utc)

      # Check due_at date
      due_passed = True  # Default to True if no due date
      if hasattr(self.lms_assignment, 'due_at') and self.lms_assignment.due_at:
        if isinstance(self.lms_assignment.due_at, str):
          due_date = dateutil.parser.parse(self.lms_assignment.due_at)
        else:
          due_date = self.lms_assignment.due_at

        # Ensure due_date has timezone info
        if due_date.tzinfo is None:
          due_date = due_date.replace(tzinfo=timezone.utc)

        due_passed = now >= due_date
        log.debug(
          f"Due date check: now={now}, due_at={due_date}, due_passed={due_passed}"
        )

      # Check lock_at date
      lock_passed = True  # Default to True if no lock date
      if hasattr(self.lms_assignment,
                 'lock_at') and self.lms_assignment.lock_at:
        if isinstance(self.lms_assignment.lock_at, str):
          lock_date = dateutil.parser.parse(self.lms_assignment.lock_at)
        else:
          lock_date = self.lms_assignment.lock_at

        # Ensure lock_date has timezone info
        if lock_date.tzinfo is None:
          lock_date = lock_date.replace(tzinfo=timezone.utc)

        lock_passed = now >= lock_date
        log.debug(
          f"Lock date check: now={now}, lock_at={lock_date}, lock_passed={lock_passed}"
        )

      # Assignment is fully locked when both dates have passed
      is_fully_locked = due_passed and lock_passed

      if not due_passed:
        log.info(
          f"Assignment not ready for grading - due date has not passed (due: {self.lms_assignment.due_at})"
        )
      elif not lock_passed:
        log.info(
          f"Assignment not ready for grading - lock date has not passed (locks: {self.lms_assignment.lock_at})"
        )
      else:
        log.debug(f"Assignment is fully locked and ready for grading")

      return is_fully_locked

    except Exception as e:
      log.warning(
        f"Error checking assignment dates: {e}. Defaulting to unlocked.")
      return False

  def get_submission_data(self) -> List[Dict]:
    """
    Return structured submission data for grading.

    Returns:
        List of dictionaries containing student_id, text, word_count, and submission_obj
    """
    return self.submission_data

  def get_all_submission_texts(self) -> List[str]:
    """
    Return just the text content from all submissions for aggregate analysis.

    Returns:
        List of submission text strings
    """
    return [data['text'] for data in self.submission_data if data['text']]

  def finalize(self, *args, **kwargs):
    """
    Finalize grading by pushing scores and feedback to Canvas.
    """
    super().finalize(*args, **kwargs)
