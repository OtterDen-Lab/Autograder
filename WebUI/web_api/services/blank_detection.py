"""
Blank detection service using population-based analysis.

This demonstrates how to process problems across all submissions using DTOs.
"""
import logging
from typing import List, Dict
from ..dtos import SubmissionDTO, ProblemDTO

log = logging.getLogger(__name__)


def apply_population_blank_detection(
    submissions: List[SubmissionDTO],
    percentile_threshold: float = 5.0
) -> None:
  """
  Apply population-based blank detection across all submissions.

  This processes each problem number across ALL submissions together,
  allowing for statistical analysis. Changes are made in-place to the DTOs.

  Args:
      submissions: List of submission DTOs to process
      percentile_threshold: Percentile cutoff for blank detection (default: 5.0)

  Example:
      >>> submissions = exam_processor.process_exams(...)
      >>> apply_population_blank_detection(submissions)
      >>> # Now submissions[0].problems[0].is_blank may be True
  """
  # Group problems by problem number
  problems_by_number: Dict[int, List[ProblemDTO]] = {}

  for submission in submissions:
    for problem in submission.problems:
      if problem.problem_number not in problems_by_number:
        problems_by_number[problem.problem_number] = []
      problems_by_number[problem.problem_number].append(problem)

  # Process each problem number separately
  for problem_num, problem_list in problems_by_number.items():
    log.info(
      f"Processing problem {problem_num}: {len(problem_list)} submissions")

    # Calculate black pixel ratios for all instances
    # (In real implementation, you'd calculate these from images)
    ratios = [_calculate_black_ratio(p.image_base64) for p in problem_list]

    # Calculate threshold from population
    import numpy as np
    threshold = np.percentile(ratios, percentile_threshold)

    log.info(
      f"Problem {problem_num}: threshold={threshold:.4f} (from {len(ratios)} submissions)"
    )

    # Apply threshold to each problem (modifies in place!)
    for i, problem in enumerate(problem_list):
      ratio = ratios[i]

      if ratio < threshold:
        problem.mark_blank(
          confidence=0.95,
          method="population",
          reasoning=
          f"Black ratio: {ratio:.4f}, Threshold (p{percentile_threshold}): {threshold:.4f}"
        )
        log.debug(
          f"Problem {problem_num} marked blank (ratio={ratio:.4f} < {threshold:.4f})"
        )
      else:
        problem.mark_not_blank()


def _calculate_black_ratio(image_base64: str) -> float:
  """
  Calculate black pixel ratio from base64 image.

  This is a placeholder - in reality you'd decode the image and analyze pixels.
  """
  import base64
  import io
  from PIL import Image
  import numpy as np

  # Decode image
  img_bytes = base64.b64decode(image_base64)
  img = Image.open(io.BytesIO(img_bytes)).convert('L')  # Convert to grayscale
  pixels = np.array(img)

  # Count black pixels (below threshold)
  black_threshold = 200  # Pixels darker than this
  black_pixels = np.sum(pixels < black_threshold)
  total_pixels = pixels.size

  return black_pixels / total_pixels if total_pixels > 0 else 0.0
