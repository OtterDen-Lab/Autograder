"""
Manual grading implementation.

Handles manual grading workflow by creating CSV files for human graders
and then loading the results back into the system.
"""
import ast
import os
import pprint

import pandas as pd

from Autograder.assignment import Assignment
from Autograder.grader import FileBasedGrader
from Autograder.registry import GraderRegistry
import Autograder.exceptions
from Autograder.lms_interface.classes import Feedback, Submission

import logging

log = logging.getLogger(__name__)


@GraderRegistry.register("Manual")
class Grader__Manual(FileBasedGrader):
  """
  Grader for manual grading workflows.

  Creates CSV files for human graders to fill out, then loads the results
  back into the grading system.

  IMPORTANT: The "total" column in the CSV should contain percentage scores (0-100+),
  where 100 = full credit. These will be automatically scaled to Canvas points.
  For example, if an assignment is worth 80 points in Canvas:
    - 100 in CSV = 80 Canvas points
    - 101 in CSV = 80.8 Canvas points (extra credit)
  """

  CSV_NAME = "grades.intermediate.csv"

  def is_grading_complete(self):
    """
    Checks to see if grading is complete.  Currently just looks for whether there is a `total` score for each entry.
    :return:
    """
    if not os.path.exists(self.CSV_NAME):
      return False

    try:
      grades_df = pd.read_csv(self.CSV_NAME)
    except (pd.errors.EmptyDataError, pd.errors.ParserError) as e:
      log.error(f"Failed to read grades CSV file {self.CSV_NAME}: {e}")
      return False
    except Exception as e:
      log.error(f"Unexpected error reading grades CSV: {e}")
      return False

    # Clean out the extra columns not associated with any submission
    grades_df = grades_df[grades_df["document_id"].notna()]

    # If there are entries missing a `total` column then we should get a different count and are incomplete
    return grades_df[grades_df["total"].notna()].shape == grades_df.shape

  def prepare(self, assignment: Assignment, *args, **kwargs):
    self.ready_to_finalize = False
    log.debug("Preparing manual grading")
    # Make a dataframe
    df = pd.DataFrame([{
      **submission.extra_info, "name":
      submission.student.name if submission.student is not None else "",
      "user_id":
      submission.student.user_id if submission.student is not None else "",
      "total":
      None
    } for submission in assignment.submissions])
    print(df.head())
    df = df.sort_values(by="document_id")

    df.to_csv(self.CSV_NAME, index=False)

  def finalize(self, assignment, *args, **kwargs):
    log.debug("Finalizing manual grading")
    if not self.is_grading_complete():
      raise Autograder.exceptions.GradingIncompleteError(
        "Some entries do not have scores. Please complete grading in the CSV file and rerun."
      )

    # Steps:
    # 1. Recreate submissions
    # 2. Pass back to assignment to remerge
    # 3. Generate grades and feedback

    # Load CSV
    grades_df = pd.read_csv(self.CSV_NAME)
    # Remove any extra information (because I like tracking my progress)
    grades_df = grades_df[grades_df["document_id"].notna()]

    # Get list of students from canvas
    # todo: this should probably be done in the `assignment`
    canvas_students_by_id = {
      s.user_id: s
      for s in assignment.lms_assignment.get_students()
    }

    graded_submissions = []

    # Make submission objects for students who have already been matched
    num_students_unmmatched = 0
    for _, row in grades_df[grades_df["user_id"].notna()].iterrows():
      try:
        log.debug(canvas_students_by_id[int(row["user_id"])])
        submission = Submission(
          student=canvas_students_by_id[int(row["user_id"])],
          status=Submission.Status.GRADED,
        )
        del canvas_students_by_id[int(row["user_id"])]
      except KeyError as e:
        log.warning(
          f"Student with user_id {row['user_id']} not found in Canvas: {e}")
        submission = Submission(student=None, status=Submission.Status.GRADED)
        num_students_unmmatched += 1
      except (ValueError, TypeError) as e:
        log.error(f"Invalid user_id format '{row['user_id']}': {e}")
        submission = Submission(student=None, status=Submission.Status.GRADED)
        num_students_unmmatched += 1

      # todo: get PDFs and comments.
      submission.feedback = Feedback(percentage_score=row["total"],
                                     comments="(Please see attached PDF)",
                                     attachments=[])
      submission.set_extra({
        "page_mappings":
        ast.literal_eval(row["page_mappings"]),
        "document_id":
        row["document_id"]
      })
      graded_submissions.append(submission)

    log.info(f"There were {len(graded_submissions)} matched canvas users.")
    log.info(f"There are {len(canvas_students_by_id)} unmatched canvas users")
    log.debug("\n" + pprint.pformat({
      id: student.name
      for id, student in canvas_students_by_id.items()
    }))

    # If we have unmatched students, exit because they should be manually matched.

    if kwargs.get("merge_only"):
      pass
    else:
      unmatched_count = len(grades_df[grades_df["user_id"].isna()])
      if unmatched_count > 0 or num_students_unmmatched > 0:
        raise Autograder.exceptions.UnmatchedStudentsError(
          f"Found {unmatched_count + num_students_unmmatched} unmatched students. "
          "Please match all students in the CSV file and re-run.")

    # Now we have a list of graded submissions
    log.info(f"We have graded {len(graded_submissions)} submissions!")
    assignment.submissions = graded_submissions
    self.ready_to_finalize = True

  def grade_assignment(self, assignment: Assignment, *args, **kwargs) -> None:
    if self.is_grading_complete():
      self.finalize(assignment, args, **kwargs)
    else:
      self.prepare(assignment, *args, **kwargs)

  def assignment_needs_preparation(self):
    return not self.is_grading_complete()

  def execute_grading(self, *args, **kwargs):
    raise NotImplementedError("Manual grading uses a different workflow")

  def score_grading(self, execution_results, *args, **kwargs) -> Feedback:
    raise NotImplementedError("Manual grading uses a different workflow")
