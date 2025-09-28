#!env python
import argparse
import contextlib
import fcntl
import os
import pprint
import shutil
import tempfile
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, List

import yaml

from lms_interface.canvas_interface import CanvasInterface, CanvasCourse, CanvasAssignment, CanvasQuiz
from Autograder.assignment import AssignmentRegistry
from Autograder.grader import GraderRegistry
from Autograder.docker_utils import DockerClient, DockerContainer
from Autograder.ai_helper import AI_Helper__Anthropic

import logging

logging.basicConfig()
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser()
  subparsers = parser.add_subparsers(dest="command", help="Available commands")

  # TEST command - for testing text submission flow
  test_parser = subparsers.add_parser("TEST", help="Test text submission flow with learning-logs.yaml")
  test_parser.add_argument("--limit", default=None, type=int)

  # Keep existing arguments for backward compatibility when no subcommand is used
  parser.add_argument("--yaml", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "example_files/programming_assignments.yaml"))
  parser.add_argument("--limit", default=None, type=int)
  parser.add_argument("--regrade", "--do_regrade", dest="do_regrade", action="store_true")
  parser.add_argument("--merge_only", dest="merge_only", action="store_true")
  parser.add_argument("--max_workers", default=None, type=int, help="Maximum number of parallel grading threads (default: number of assignments)")
  parser.add_argument("--test", action="store_true", help="Only downloads for test student")

  args = parser.parse_args()

  # Handle TEST command
  if args.command == "TEST":
    args.yaml = os.path.join(os.path.dirname(os.path.abspath(__file__)), "example_files/learning-logs.yaml")
    args.regrade = True
    args.text = True
    args.max_workers = 1

  return args


@contextlib.contextmanager
def ensure_single_instance():
  """
  Context manager for file locking to prevent multiple instances.

  Ensures only one grading process runs at a time to avoid conflicts
  with Docker and Canvas operations.
  """
  lockfile = "/tmp/TeachingTools.grade_assignments.lock"
  lock_fd = open(lockfile, "w")
  try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    yield
  except IOError as e:
    log.warning("Early exiting because another instance is already running")
    log.warning(e)
    raise SystemExit(0)
  finally:
    try:
      lock_fd.close()
    except Exception:
      pass


def grade_single_assignment(assignment_data: Dict) -> Dict:
  """
  Grade a single assignment in a separate thread.
  
  Args:
    assignment_data: Dict containing all data needed to grade one assignment
  
  Returns:
    Dict with grading results and any errors
  """
  thread_id = threading.current_thread().ident
  assignment_id = None  # Initialize for error handling
  try:
    course = assignment_data['course']
    yaml_assignment = assignment_data['yaml_assignment']
    merged_assignment = assignment_data['merged_assignment']
    args = assignment_data['args']
    push_grades = assignment_data['push_grades']
    
    assignment_id = yaml_assignment['id']
    assignment_type = merged_assignment.get('type', 'assignment')  # Default to assignment

    # Create assignment or quiz object based on type
    if assignment_type.lower() == 'quiz':
      lms_assignment = course.get_quiz(assignment_id)
      log.info(f"[Thread {thread_id}] Grading quiz \"{lms_assignment.name}\"")
    else:
      lms_assignment = course.get_assignment(assignment_id)
      log.info(f"[Thread {thread_id}] Grading assignment \"{lms_assignment.name}\"")

    assignment_grading_kwargs = merged_assignment.get('kwargs', {})
    assignment_grading_kwargs["course_name"] = assignment_data.get("course_name")
    do_regrade = args.do_regrade
    
    # Get the grader from the registry
    grader_name = merged_assignment.get("grader")
    repo_path = merged_assignment.get('repo_path')
    
    # Create grader with assignment identifier for better logging
    assignment_name = lms_assignment.name.split()[0]
    grader = GraderRegistry.create(
      grader_name,
      assignment_path=repo_path,
      assignment_name=assignment_name,
      **assignment_grading_kwargs
    )
    
    with tempfile.TemporaryDirectory() as working_dir:
      # Focus on the given assignment
      with AssignmentRegistry.create(
          merged_assignment['kind'],
          lms_assignment=lms_assignment,
          grading_root_dir=working_dir,
          **merged_assignment.get('assignment_kwargs', {})
      ) as grading_assignment:
        
        # If the grader doesn't need preparation, skip the prep step
        if grader.assignment_needs_preparation():
          # For manual grading, we'll skip the interactive prompt in multi-threaded mode
          if grader_name.lower() in ["manual"]:
            log.warning(f"[Thread {thread_id}] Manual grading detected for {lms_assignment.name} - skipping interactive prompts in multi-threaded mode")
          
          grading_assignment.prepare(
            limit=args.limit,
            do_regrade=do_regrade,
            merge_only=args.merge_only,
            test=args.test,
            **merged_assignment.get("kwargs", {})
          )
          
        grader.grade_assignment(grading_assignment, **assignment_grading_kwargs, merge_only=args.merge_only, do_regrade=do_regrade)
        
        for submission in grading_assignment.submissions:
          log.info(f"{submission}")
        
        if grader.ready_to_finalize:
          if grader_name.lower() in ["manual"]:
            log.warning(f"[Thread {thread_id}] Manual grading finalization for {lms_assignment.name} - skipping interactive prompts in multi-threaded mode")
          # Check for record retention setting and determine records directory
          record_retention = merged_assignment.get('record_retention', False)
          if record_retention:
            # Determine where to save records
            records_dir = merged_assignment.get('records_dir')
            if records_dir is None:
              # Default to 'records' directory in the main project directory
              records_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "records")
            else:
              # Expand user paths (like ~/records)
              records_dir = os.path.expanduser(records_dir)
            
            grading_assignment.finalize(
              push=push_grades, 
              merge_only=args.merge_only, 
              record_retention=record_retention,
              records_dir=records_dir
            )
          else:
            grading_assignment.finalize(push=push_grades, merge_only=args.merge_only)
    
    return {
      'success': True,
      'assignment_name': lms_assignment.name,
      'assignment_id': assignment_id,
      'thread_id': thread_id
    }
    
  except Exception as e:
    log.error(f"[Thread {thread_id}] Error grading assignment {assignment_id or 'unknown'}: {e}")
    log.error(f"[Thread {thread_id}] Traceback: {traceback.format_exc()}")
    return {
      'success': False,
      'assignment_id': assignment_id,
      'error': str(e),
      'thread_id': thread_id
    }
  finally:
    # Ensure cleanup always happens, even if errors occurred
    try:
      if 'grader' in locals():
        grader.cleanup()
        log.debug(f"[Thread {thread_id}] Cleanup completed for assignment {assignment_id or 'unknown'}")
    except Exception as cleanup_error:
      log.warning(f"[Thread {thread_id}] Error during cleanup: {cleanup_error}")


def load_and_validate_config(yaml_path: str) -> Dict:
  """
  Load YAML configuration and extract global settings.
  
  Args:
    yaml_path: Path to the YAML configuration file
    
  Returns:
    Dictionary containing the loaded configuration
  """
  with open(yaml_path) as fid:
    grader_info = yaml.safe_load(fid)
  
  log.debug(f"grader_info: {grader_info}")
  return grader_info


def create_assignment_data(
    course,
    course_name,
    yaml_assignment: Dict,
    merged_assignment: Dict,
    args: argparse.Namespace,
    push_grades: bool
) -> Dict:
  """
  Create assignment data structure for grading.
  
  Args:
    course: Canvas course object
    yaml_assignment: Assignment configuration from YAML
    merged_assignment: Merged assignment configuration
    args: Command line arguments
    push_grades: Whether to push grades to LMS
    
  Returns:
    Dictionary containing assignment data for grading
  """
  assignment_id = yaml_assignment['id']
    
  return {
    'course': course,
    'course_name': course_name,
    'yaml_assignment': yaml_assignment,
    'merged_assignment': merged_assignment,
    'args': args,
    'push_grades': push_grades,
  }


def collect_assignments_to_grade(config: Dict, args: argparse.Namespace) -> List[Dict]:
  """
  Process courses and collect all assignments that need grading.
  
  Args:
    config: Loaded YAML configuration
    args: Command line arguments
    
  Returns:
    List of assignment data dictionaries ready for grading
  """
  # Pull flags from YAML file that will be applied to all submissions
  use_prod = config.get('prod', False)
  push_grades = config.get('push', False)
  
  # Create the LMS interface
  lms_interface = CanvasInterface(prod=use_prod)
  
  assignments_to_grade = []
  
  # Walk through all defined courses, error if we don't have required information
  for yaml_course in config.get('courses', []):
    try:
      course_id = int(yaml_course['id'])
    except KeyError as e:
      log.error("No course ID specified. Please update.")
      log.error(f"{pprint.pformat(yaml_course)}")
      log.error(e)
      raise SystemExit(1)
    
    # Create course object if found
    course = lms_interface.get_course(course_id)
    log.info(f"Preparing to grade Course \"{course.name}\"")
    
    # Get course-level defaults
    course_defaults = yaml_course.get('assignment_defaults', {})
    course_grader = yaml_course.get('grader')
    
    # Walk through assignments in course to grade, error if we don't have required information
    for yaml_assignment in yaml_course.get('assignments', []):
      if yaml_assignment.get('disabled', False):
        continue
      try:
        assignment_id = yaml_assignment['id']
      except KeyError as e:
        log.error("No assignment ID specified. Please update.")
        log.error(f"{pprint.pformat(yaml_course)}")
        log.error(e)
        raise SystemExit(1)
      
      # Merge course defaults with assignment-specific settings
      merged_assignment = {}
      merged_assignment.update(course_defaults)
      merged_assignment.update(yaml_assignment)
      
      # Merge kwargs specifically (deep merge)
      merged_kwargs = {}
      merged_kwargs.update(course_defaults.get('kwargs', {}))
      merged_kwargs.update(yaml_assignment.get('kwargs', {}))
      merged_assignment['kwargs'] = merged_kwargs
      
      # Use course default grader if not specified at assignment level
      if 'grader' not in merged_assignment:
        merged_assignment['grader'] = course_grader or "Dummy"
      
      # Add this assignment to our list to be graded
      assignment_data = create_assignment_data(
        course,
        yaml_course.get("name"),
        yaml_assignment,
        merged_assignment,
        args,
        push_grades
      )
      assignments_to_grade.append(assignment_data)
  
  return assignments_to_grade


def execute_grading(assignments_to_grade: List[Dict], args: argparse.Namespace) -> List[Dict]:
  """
  Execute grading either single-threaded or multi-threaded.
  
  Args:
    assignments_to_grade: List of assignment data for grading
    args: Command line arguments
    
  Returns:
    List of grading results
  """
  log.info(f"Found {len(assignments_to_grade)} assignments to grade")
  
  # Determine number of worker threads
  max_workers = args.max_workers
  if max_workers is None:
    max_workers = min(len(assignments_to_grade), 4)  # Default to 4 or number of assignments, whichever is smaller
  
  log.info(f"Using {max_workers} worker threads for grading")
  
  # Grade assignments in parallel
  results = []
  # Multi-threaded execution
  log.info("Running in multi-threaded mode")
  with ThreadPoolExecutor(max_workers=max_workers) as executor:
    # Submit all assignments for grading
    future_to_assignment = {
      executor.submit(grade_single_assignment, assignment_data): assignment_data
      for assignment_data in assignments_to_grade
    }
    
    # Collect results as they complete
    for future in as_completed(future_to_assignment):
      assignment_data = future_to_assignment[future]
      try:
        result = future.result()
        results.append(result)
        
        if result['success']:
          log.info(f"Successfully graded assignment {result['assignment_name']} (ID: {result['assignment_id']})")
        else:
          log.error(f"Failed to grade assignment {result['assignment_id']}: {result['error']}")
          
      except Exception as exc:
        log.error(f"Assignment {assignment_data['yaml_assignment']['id']} generated an exception: {exc}")
        results.append({
          'success': False,
          'assignment_id': assignment_data['yaml_assignment']['id'],
          'error': str(exc)
        })
  
  return results


def print_results_summary(results: List[Dict]) -> None:
  """
  Print summary of grading results.
  
  Args:
    results: List of grading result dictionaries
  """
  successful = sum(1 for r in results if r['success'])
  failed = len(results) - successful
  
  log.info(f"Grading completed: {successful} successful, {failed} failed")
  
  if failed > 0:
    log.error("The following assignments failed:")
    for result in results:
      if not result['success']:
        log.error(f"  Assignment {result['assignment_id']}: {result['error']}")


def analyze_submissions_aggregate(all_submissions: List[str], assignment_name: str) -> Dict:
  """
  Perform aggregate analysis across all submissions to identify patterns and extract probable topics.

  Args:
    all_submissions: List of all submission text content
    assignment_name: Name of the assignment for context

  Returns:
    Dictionary with analysis results and probable topics
  """
  log.info("Performing aggregate analysis of all submissions...")

  # Combine all submissions for aggregate analysis
  combined_text = "\n\n---SUBMISSION SEPARATOR---\n\n".join(all_submissions)

  prompt = f"""
You are analyzing student learning log submissions for an assignment called "{assignment_name}".

Please analyze these {len(all_submissions)} student submissions and return a JSON response with:

{{
  "common_themes": "What concepts or topics are most students discussing?",
  "key_insights": "What seems to be sticking with students vs. what they're struggling with?",
  "learning_patterns": "Are there recurring learning patterns or misconceptions?",
  "teaching_feedback": "Based on these submissions, what feedback would help the instructor improve their teaching?",
  "core_topics": ["exactly", "5", "most", "important", "general", "topics"]
}}

For core_topics, identify the 5 most important and general topics that best summarize what was covered in class this week. These should be:
- Broad enough to encompass multiple related concepts students discussed
- The most fundamental/important topics from the class session
- Topics that multiple students engaged with (directly or indirectly)
- General categories rather than very specific technical terms

Here are the submissions:

{combined_text}

Return only valid JSON.
"""

  try:
    # Use OpenAI for JSON response since it has better JSON formatting
    from Autograder.ai_helper import AI_Helper__OpenAI
    ai_helper = AI_Helper__OpenAI()
    result = ai_helper.query_ai(prompt, [], max_response_tokens=2000)
    return result
  except Exception as e:
    log.error(f"Error in aggregate analysis: {e}")
    log.error(f"Falling back to Anthropic...")
    # Fallback to Anthropic if OpenAI fails
    try:
      ai_helper = AI_Helper__Anthropic()
      analysis_text = ai_helper.query_ai(prompt, [], max_response_tokens=2000)
      # Try to parse any JSON that might be in the response
      import json
      import re
      json_match = re.search(r'\{.*\}', analysis_text, re.DOTALL)
      if json_match:
        result = json.loads(json_match.group())
        return result
      else:
        # If no JSON found, return the text analysis in a structured way
        return {
          "common_themes": analysis_text,
          "key_insights": "",
          "learning_patterns": "",
          "teaching_feedback": "",
          "probable_topics": []
        }
    except Exception as fallback_error:
      log.error(f"Fallback also failed: {fallback_error}")
      return {
        "common_themes": f"Error performing analysis: {e}",
        "key_insights": "",
        "learning_patterns": "",
        "teaching_feedback": "",
        "probable_topics": []
      }


def check_individual_submission(submission_text: str, student_id: str, expected_topics: List[str]) -> Dict:
  """
  Check individual submission for topic coverage and engagement.

  Args:
    submission_text: The student's submission text
    student_id: Student identifier
    expected_topics: List of topics that should be covered

  Returns:
    Dictionary with analysis results
  """
  log.debug(f"Checking individual submission for student {student_id}...")

  topics_str = ", ".join(expected_topics)
  prompt = f"""
You are analyzing a student's learning log submission for grading and support identification. Learning logs are study tools where students explain topics to their future selves. The instructor emphasizes: "the best way to make a study guide and are for you, because I know this material already -- write it for your future self."

These GENERAL topics were covered in class: {topics_str}

GRADING RUBRIC (Total: 10 points):
- Completion (4 pts): Based on genuine effort and depth of reflection
- Length (2 pts): ≥250 words gets 2/2, <250 words gets 0/2
- Relevance (2 pts): Addresses class material (2=covers 3+ topics, 1=covers 1-2, 0=off-topic)
- Explanation Effort (2 pts): Attempts to explain concepts for future self, even if confused

Please analyze this submission and return a JSON response with:
{{
  "completion_score": "4, 3, 2, 1, or 0 based on depth of reflection and genuine effort",
  "relevance_score": "2, 1, or 0 based on topic coverage",
  "explanation_effort_score": "2, 1, or 0 based on attempt to explain vs. just list facts",
  "topics_covered": ["list", "of", "general", "class", "topics", "that", "relate", "to", "student", "content"],
  "topics_missing": ["list", "of", "general", "class", "topics", "not", "addressed"],
  "word_count": approximate_word_count_number,
  "needs_support": "true/false - student shows significant confusion or struggle that warrants office hours suggestion",
  "support_reason": "brief explanation if needs_support is true, empty string if false",
  "feedback": "supportive guidance to help the student write more reflectively for better studying"
}}

SCORING GUIDELINES:
- Completion: Reward genuine engagement with learning, even if confused. Penalize only minimal effort.
- Explanation Effort: Full points for trying to work through concepts in their own words, even if incorrect.
- A confused student genuinely trying to understand should get high completion and explanation scores.

For feedback, focus on study strategies and encouraging deeper reflection rather than corrections.

Student submission:
{submission_text}

Return only valid JSON.
"""

  try:
    # Use OpenAI for consistent JSON response
    from Autograder.ai_helper import AI_Helper__OpenAI
    ai_helper = AI_Helper__OpenAI()
    result = ai_helper.query_ai(prompt, [], max_response_tokens=1000)
    result["student_id"] = student_id
    return result
  except Exception as e:
    log.error(f"Error analyzing submission for student {student_id}: {e}")
    log.error(f"Falling back to Anthropic for individual analysis...")
    # Fallback to Anthropic if OpenAI fails
    try:
      ai_helper = AI_Helper__Anthropic()
      analysis_text = ai_helper.query_ai(prompt, [], max_response_tokens=1000)
      # Try to parse any JSON that might be in the response
      import json
      import re
      json_match = re.search(r'\{.*\}', analysis_text, re.DOTALL)
      if json_match:
        result = json.loads(json_match.group())
        result["student_id"] = student_id
        return result
      else:
        # If no JSON found, create a structured response from the text
        return {
          "student_id": student_id,
          "topics_covered": [],
          "topics_missing": expected_topics,
          "engagement_level": "medium",
          "word_count": len(submission_text.split()),
          "feedback": analysis_text[:200] + "..." if len(analysis_text) > 200 else analysis_text
        }
    except Exception as fallback_error:
      log.error(f"Fallback also failed for student {student_id}: {fallback_error}")
      return {
        "student_id": student_id,
        "topics_covered": [],
        "topics_missing": expected_topics,
        "engagement_level": "error",
        "word_count": len(submission_text.split()),
        "feedback": f"Error analyzing submission: {e}"
      }


def test(args: argparse.Namespace) -> None:
  """
  Test function for experimenting with text submission assignments.

  Args:
    args: Command line arguments
  """
  log.info("Running TEST mode for text submissions...")

  config = load_and_validate_config(args.yaml)

  # Pull flags from YAML file
  use_prod = config.get('prod', False)

  # Create the LMS interface
  lms_interface = CanvasInterface(prod=use_prod)

  # Get the first course and assignment for testing
  if not config.get('courses'):
    log.error("No courses found in configuration")
    return

  yaml_course = config['courses'][0]
  course_id = int(yaml_course['id'])
  course = lms_interface.get_course(course_id)
  log.info(f"Testing with Course \"{course.name}\" (ID: {course_id})")

  if not yaml_course.get('assignments'):
    log.error("No assignments found in course configuration")
    return

  yaml_assignment = yaml_course['assignments'][0]
  assignment_id = yaml_assignment['id']

  # Get the assignment from Canvas
  lms_assignment = course.get_assignment(assignment_id)
  log.info(f"Testing with Assignment \"{lms_assignment.name}\" (ID: {assignment_id})")

  # Pull submissions for this assignment
  log.info("Fetching submissions...")
  submissions = lms_assignment.get_submissions()

  # Collect all submission data
  all_submission_texts = []
  submission_data = []

  log.info(f"Found {len(submissions)} submissions")
  for i, submission in enumerate(submissions[:args.limit] if args.limit else submissions):  # Limit to specified limit or all submissions
    log.info(f"Processing submission {i+1}: User ID {submission.student} ({len(submission.submission_text.split())} words)")
    submission_contents = ' '.join(submission.submission_text)

    all_submission_texts.append(submission_contents)
    submission_data.append({
      'student_id': submission.student,
      'text': submission_contents,
      'word_count': len(submission_contents.split())
    })

  # Phase 1: Aggregate Analysis
  log.info("\n" + "="*50)
  log.info("PHASE 1: AGGREGATE ANALYSIS")
  log.info("="*50)

  aggregate_analysis = analyze_submissions_aggregate(all_submission_texts, lms_assignment.name)

  # Pretty print the structured analysis
  log.info("\n📊 COMMON THEMES:")
  log.info(aggregate_analysis.get("common_themes", "No themes identified"))

  log.info("\n💡 KEY INSIGHTS:")
  log.info(aggregate_analysis.get("key_insights", "No insights available"))

  log.info("\n🔄 LEARNING PATTERNS:")
  log.info(aggregate_analysis.get("learning_patterns", "No patterns identified"))

  log.info("\n🎯 TEACHING FEEDBACK:")
  log.info(aggregate_analysis.get("teaching_feedback", "No feedback available"))

  core_topics = aggregate_analysis.get("core_topics", [])
  log.info(f"\n📝 CORE TOPICS ({len(core_topics)} identified):")
  for topic in core_topics:
    log.info(f"  • {topic}")

  # Phase 2: Individual Topic Coverage
  log.info("\n" + "="*50)
  log.info("PHASE 2: INDIVIDUAL TOPIC COVERAGE")
  log.info("="*50)

  # Use the 5 core topics identified by the AI
  all_expected_topics = core_topics

  log.info(f"Checking for coverage of {len(all_expected_topics)} topics:")
  for topic in all_expected_topics:
    log.info(f"  • {topic}")
  log.info("")

  for submission_info in submission_data:
    individual_analysis = check_individual_submission(
      submission_info['text'],
      submission_info['student_id'],
      all_expected_topics
    )

    # Calculate grade and format results
    student_id = individual_analysis.get("student_id", "unknown")
    completion_score = individual_analysis.get("completion_score", 0)
    relevance_score = individual_analysis.get("relevance_score", 0)
    explanation_effort_score = individual_analysis.get("explanation_effort_score", 0)
    word_count = individual_analysis.get("word_count", 0)
    topics_covered = individual_analysis.get("topics_covered", [])
    topics_missing = individual_analysis.get("topics_missing", [])
    needs_support = individual_analysis.get("needs_support", False)
    support_reason = individual_analysis.get("support_reason", "")
    feedback = individual_analysis.get("feedback", "No feedback available")

    # Calculate length score based on word count
    length_score = 2 if word_count >= 250 else 0

    # Calculate total grade
    total_grade = completion_score + length_score + relevance_score + explanation_effort_score

    log.info(f"🧑‍🎓 STUDENT {student_id}:")
    log.info(f"   📊 GRADE: {total_grade}/10 (Completion: {completion_score}/4, Length: {length_score}/2, Relevance: {relevance_score}/2, Explanation: {explanation_effort_score}/2)")
    log.info(f"   📝 Words: {word_count} | Topics: {len(topics_covered)}/{len(all_expected_topics)}")
    log.info(f"   ✅ Covered: {', '.join(topics_covered) if topics_covered else 'None'}")
    if topics_missing:
      log.info(f"   ❌ Missing: {', '.join(topics_missing)}")

    if needs_support:
      log.info(f"   🆘 NEEDS SUPPORT: {support_reason}")

    log.info(f"   💬 Feedback: {feedback}")
    log.info("")

  # Summary of students needing support
  support_needed = []
  for submission_info in submission_data:
    individual_analysis = check_individual_submission(
      submission_info['text'],
      submission_info['student_id'],
      all_expected_topics
    )
    if individual_analysis.get("needs_support", False):
      support_needed.append({
        "student_id": individual_analysis.get("student_id"),
        "reason": individual_analysis.get("support_reason", "")
      })

  if support_needed:
    log.info("\n" + "="*50)
    log.info("STUDENTS WHO MIGHT BENEFIT FROM OFFICE HOURS")
    log.info("="*50)
    for student in support_needed:
      log.info(f"• {student['student_id']}: {student['reason']}")
  else:
    log.info("\n📈 All students appear to be engaging well with the material!")


def main() -> None:
  """
  Main entry point for the grading script.

  Coordinates the entire grading process using a clean, modular approach.
  """
  args = parse_args()

  # Handle TEST command
  if args.command == "TEST":
    test(args)
    return

  with ensure_single_instance():
    try:
      config = load_and_validate_config(args.yaml)

      assignments_to_grade = collect_assignments_to_grade(config, args)
      results = execute_grading(assignments_to_grade, args)

      print_results_summary(results)
    finally:
      # Always perform global Docker cleanup at the end
      log.info("Performing final Docker cleanup...")
      DockerClient.cleanup()


if __name__ == "__main__":
  main()

