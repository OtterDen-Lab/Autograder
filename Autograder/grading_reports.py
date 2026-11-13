"""Shared grading error reporting, independent of execution backend."""
import logging
from typing import Optional, Tuple

log = logging.getLogger(__name__)


class GradingErrorReporter:
  def _get_slack_config(self) -> dict:
    """
    Get Slack configuration with support for override hierarchy.

    Configuration is checked in order (most specific to least specific):
    1. Assignment-level config (self.slack_webhook, self.slack_channel, self.report_errors)
    2. Course-level config via environment variables (SLACK_WEBHOOK_{COURSE}, etc.)
    3. Global config via environment variables (SLACK_BOT_TOKEN, SLACK_WEBHOOK, etc.)

    Returns:
        dict with keys: webhook, token, channel, enabled
    """
    import os

    # Assignment-level overrides
    webhook = getattr(self, 'slack_webhook', None)
    token = getattr(self, 'slack_token', None)
    channel = getattr(self, 'slack_channel', None)
    enabled = getattr(self, 'report_errors', True)  # Default to enabled

    # Course-level environment variables (if course_name is set)
    course_name = getattr(self, 'course_name',
                          '').upper().replace(' ', '_').replace('-', '_')
    if course_name:
      webhook = webhook or os.getenv(f'SLACK_WEBHOOK_{course_name}')
      token = token or os.getenv(f'SLACK_BOT_TOKEN_{course_name}')
      channel = channel or os.getenv(f'SLACK_CHANNEL_{course_name}')

      # Check for course-specific opt-out
      course_enabled = os.getenv(f'REPORT_ERRORS_{course_name}')
      if course_enabled is not None:
        enabled = course_enabled.lower() not in ('false', '0', 'no')

    # Global environment variables
    webhook = webhook or os.getenv('SLACK_WEBHOOK')
    token = token or os.getenv('SLACK_BOT_TOKEN')
    channel = channel or os.getenv('SLACK_CHANNEL')

    # Global opt-out
    global_enabled = os.getenv('REPORT_ERRORS')
    if global_enabled is not None:
      enabled = enabled and global_enabled.lower() not in ('false', '0', 'no')

    return {
      'webhook': webhook,
      'token': token,
      'channel': channel,
      'enabled': enabled
    }

  def _zip_student_files(self, submission) -> Optional[bytes]:
    """
    Create a zip archive of student submission files.

    Args:
        submission: Submission object with files attribute

    Returns:
        Zip file as bytes, or None if no files
    """
    import io
    import zipfile

    if not submission or not hasattr(submission,
                                     'files') or not submission.files:
      return None

    try:
      zip_buffer = io.BytesIO()
      with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for file_obj in submission.files:
          # Get file name
          filename = getattr(file_obj, 'name', 'unnamed_file')

          # Read file content
          file_obj.seek(0)
          content = file_obj.read()

          # Add to zip
          zip_file.writestr(filename, content)

          # Reset file pointer
          file_obj.seek(0)

      zip_buffer.seek(0)
      return zip_buffer.getvalue()
    except Exception as e:
      log.warning(f"Failed to zip student files: {e}")
      return None

  def _send_error_to_slack(self,
                           error_msg: str,
                           execution_results: Tuple,
                           submission=None) -> None:
    """
    Send error report to Slack with file attachments.

    Args:
        error_msg: Description of the error
        execution_results: Tuple of (rc, stdout, stderr) from execution
        submission: The submission object being graded (if available)
    """
    import io
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    slack_config = self._get_slack_config()

    if not slack_config['enabled']:
      log.debug("Error reporting is disabled")
      return

    # Need either webhook or (token + channel)
    webhook = slack_config['webhook']
    token = slack_config['token']
    channel = slack_config['channel']

    if not webhook and not (token and channel):
      log.debug(
        f"Slack not configured for error reporting (webhook={webhook}, token={bool(token)}, channel={channel})"
      )
      return

    rc, stdout, stderr = execution_results

    # Get student info
    student_name = "Unknown"
    if submission and hasattr(submission, 'student') and submission.student:
      student_name = submission.student.name

    # Build initial message
    assignment_name = getattr(self, 'assignment_name', 'Unknown Assignment')
    course_name = getattr(self, 'course_name', 'Unknown Course')

    def _decode_snippet(payload, max_chars=700):
      if payload is None:
        return ""
      if isinstance(payload, bytes):
        text = payload.decode("utf-8", errors="replace")
      else:
        text = str(payload)
      text = text.strip()
      if len(text) > max_chars:
        return text[:max_chars] + "... [truncated]"
      return text

    stdout_snippet = _decode_snippet(stdout)
    stderr_snippet = _decode_snippet(stderr)

    message = (f":warning: *Grading Error Detected*\n"
               f"*Course:* {course_name}\n"
               f"*Assignment:* {assignment_name}\n"
               f"*Student:* {student_name}\n"
               f"*Error:* {error_msg}\n"
               f"*Return Code:* {rc}")
    if not self.upload_error_artifacts:
      message += ("\n*Artifact mode:* disabled (`upload_error_artifacts=false`)\n"
                  "Only summary details are included to reduce PII exposure.")
    else:
      message += ("\n:rotating_light: *Artifact mode enabled* - "
                  "attachments may include PII/student code.")
    if stdout_snippet:
      message += f"\n\n*stdout (truncated):*\n```{stdout_snippet}```"
    if stderr_snippet:
      message += f"\n\n*stderr (truncated):*\n```{stderr_snippet}```"

    try:
      # Handle webhook case (simple message only, no files)
      if webhook:
        import requests
        response = requests.post(webhook, json={"text": message}, timeout=10)
        if response.status_code == 200:
          log.info("Slack error notification sent via webhook (no files)")
        else:
          log.warning(f"Slack webhook failed: {response.status_code}")
        return

      # Use Slack SDK for bot token
      client = WebClient(token=token)
      if not self.upload_error_artifacts:
        client.chat_postMessage(channel=channel,
                                text=message,
                                mrkdwn=True,
                                unfurl_links=False,
                                unfurl_media=False)
        log.info("Slack error report sent successfully (summary mode)")
        return

      # Prepare files to upload
      files_to_upload = []

      # 1. Error details as text file
      error_details = (f"Error: {error_msg}\n"
                       f"Return Code: {rc}\n"
                       f"Student: {student_name}\n"
                       f"Assignment: {assignment_name}\n"
                       f"Course: {course_name}\n")
      files_to_upload.append(('error_details.txt', error_details.encode()))

      # 2. stdout
      if stdout:
        files_to_upload.append(
          ('stdout.txt',
           stdout if isinstance(stdout, bytes) else stdout.encode()))

      # 3. stderr
      if stderr:
        files_to_upload.append(
          ('stderr.txt',
           stderr if isinstance(stderr, bytes) else stderr.encode()))

      # 4. feedback.yaml (if exists)
      feedback_content = getattr(execution_results, "files", {}).get("/tmp/feedback.yaml")
      if feedback_content is None:
        read_file = getattr(self, "read_file_from_container", None)
        if callable(read_file):
          feedback_content = read_file("/tmp/feedback.yaml")
      if feedback_content:
        files_to_upload.append(('feedback.yaml', feedback_content.encode()))

      # 5. Student code as zip
      student_zip = self._zip_student_files(submission)
      if student_zip:
        files_to_upload.append(('student_code.zip', student_zip))

      # Upload all files together with the message (not threaded)
      # Note: Slack's files_upload_v2 doesn't support multiple files in one call,
      # so we upload them separately but they'll all appear as attachments
      for filename, content in files_to_upload:
        try:
          client.files_upload_v2(
            channel=channel,
            file=io.BytesIO(content),
            filename=filename,
            title=filename,
            initial_comment=message if filename == files_to_upload[0][0] else
            None  # Only show message with first file
          )
        except SlackApiError as e:
          log.warning(f"Failed to upload {filename}: {e.response['error']}")
        except Exception as e:
          log.warning(f"Failed to upload {filename}: {e}")

      log.info("Slack error report sent successfully with attachments")

    except SlackApiError as e:
      log.warning(f"Slack API error: {e.response['error']}")
    except Exception as e:
      log.warning(f"Failed to send Slack error report: {e}")

  def _report_grading_error(self,
                            error_msg: str,
                            execution_results: Tuple,
                            submission=None) -> None:
    """
    Hook for reporting grading errors (e.g., via Slack, email, etc.).

    Default implementation logs the error and sends to Slack if configured.
    Subclasses can override to add custom behavior or provide additional context.

    Args:
        error_msg: Description of the error
        execution_results: Tuple of (rc, stdout, stderr) from execution
        submission: The submission object being graded (if available)
    """
    log.error(f"Grading error hook called: {error_msg}")
    if submission:
      log.error(
        f"  Submission: {submission.student.name if hasattr(submission, 'student') else 'Unknown'}"
      )
    rc, stdout, stderr = execution_results
    log.error(f"  Return code: {rc}")
    log.error(f"  Stdout (first 500 chars): {stdout[:500] if stdout else ''}")
    log.error(f"  Stderr (first 500 chars): {stderr[:500] if stderr else ''}")

    # Send to Slack if configured
    self._send_error_to_slack(error_msg, execution_results, submission)

