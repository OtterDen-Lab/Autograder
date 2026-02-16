"""
PII (Personally Identifiable Information) redaction for text submissions.

This module provides best-effort PII redaction before sending submission text
to LLM providers. It handles common patterns like emails, phone numbers,
student IDs, and explicit student names.

This module is designed to be reusable across different grading contexts.
"""

import re
from typing import Dict


class SubmissionPIIRedactor:
    """
    Best-effort PII redaction for submission text before LLM calls.

    Supports redaction of:
    - Email addresses
    - Phone numbers (various formats)
    - Student ID patterns
    - Name headers (e.g., "Name: John Doe")
    - Explicit student names when provided
    - Explicit student IDs when provided
    """

    EMAIL_PATTERN = re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    PHONE_PATTERN = re.compile(
        r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?){2}\d{4}\b")
    STUDENT_ID_PATTERN = re.compile(
        r"\b(?:student\s*(?:id|number)|sid|canvas_user_id)\s*[:#=]?\s*\d{4,}\b",
        re.IGNORECASE)
    NAME_HEADER_PATTERN = re.compile(r"(?im)^\s*name\s*:\s*.+$")

    @staticmethod
    def _replace_all(pattern: re.Pattern, text: str,
                     replacement: str) -> tuple[str, int]:
        """Replace all matches of pattern in text, returning (new_text, count)."""
        return pattern.subn(replacement, text)

    @staticmethod
    def _compile_explicit_name_pattern(student_name: str | None) -> re.Pattern | None:
        """
        Compile a pattern to match the explicit student name.

        Args:
            student_name: The student's name to redact

        Returns:
            Compiled regex pattern or None if name is invalid
        """
        if not student_name:
            return None
        normalized = " ".join(str(student_name).split())
        if len(normalized) < 3:
            return None

        escaped = re.escape(normalized).replace(r"\ ", r"\s+")
        return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)

    def redact(self,
               text: str,
               *,
               student_name: str | None = None,
               student_id: int | str | None = None) -> tuple[str, Dict[str, int]]:
        """
        Redact PII from submission text.

        Args:
            text: The submission text to redact
            student_name: Optional explicit student name to redact
            student_id: Optional explicit student ID to redact

        Returns:
            Tuple of (redacted_text, counts_dict) where counts_dict contains
            the number of replacements made for each category
        """
        if not text:
            return text, {"total_replacements": 0}

        redacted = text
        counts: Dict[str, int] = {}

        # Redact common patterns
        redacted, count = self._replace_all(self.EMAIL_PATTERN, redacted,
                                            "[REDACTED_EMAIL]")
        counts["emails"] = count

        redacted, count = self._replace_all(self.PHONE_PATTERN, redacted,
                                            "[REDACTED_PHONE]")
        counts["phones"] = count

        redacted, count = self._replace_all(self.STUDENT_ID_PATTERN, redacted,
                                            "[REDACTED_STUDENT_ID]")
        counts["student_id_markers"] = count

        redacted, count = self._replace_all(self.NAME_HEADER_PATTERN, redacted,
                                            "Name: [REDACTED_NAME]")
        counts["name_headers"] = count

        # Redact explicit student name if provided
        name_pattern = self._compile_explicit_name_pattern(student_name)
        if name_pattern is not None:
            redacted, count = name_pattern.subn("[REDACTED_NAME]", redacted)
            counts["explicit_student_name"] = count

        # Redact explicit student ID if provided
        if student_id is not None:
            escaped_id = re.escape(str(student_id))
            if escaped_id:
                id_pattern = re.compile(rf"\b{escaped_id}\b")
                redacted, count = id_pattern.subn("[REDACTED_STUDENT_ID]", redacted)
                counts["explicit_student_id"] = count

        counts["total_replacements"] = sum(counts.values())
        return redacted, counts
