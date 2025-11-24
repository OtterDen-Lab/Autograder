"""
Custom exception hierarchy for the Autograder system.

Provides structured exception handling with specific error types
for different components of the grading system.
"""


class AutograderError(Exception):
  """Base exception for all autograder operations."""
  pass


class GradingError(AutograderError):
  """Errors that occur during grading execution."""
  pass


class DockerError(AutograderError):
  """Errors related to Docker operations."""
  pass


class ContainerError(DockerError):
  """Errors related to container lifecycle and execution."""
  pass


class ImageBuildError(DockerError):
  """Errors that occur during Docker image building."""
  pass


class LMSError(AutograderError):
  """Errors related to LMS integration and API calls."""
  pass


class SubmissionError(AutograderError):
  """Errors related to student submission processing."""
  pass


class ConfigurationError(AutograderError):
  """Errors in system configuration or setup."""
  pass


class StudentMatchingError(AutograderError):
  """Errors that occur when matching students to submissions."""
  pass


class FileProcessingError(AutograderError):
  """Errors that occur during file operations."""
  pass


class GradingIncompleteError(GradingError):
  """Raised when grading is not complete but expected to be."""
  pass


class UnmatchedStudentsError(StudentMatchingError):
  """Raised when there are unmatched students that must be resolved."""
  pass
