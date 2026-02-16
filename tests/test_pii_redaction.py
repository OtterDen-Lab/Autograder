"""
Comprehensive tests for PII redaction in text submissions.

Tests edge cases for:
- Unicode names (José García, 李明, Müller)
- Names with apostrophes (O'Brien, O'Connor)
- Multi-word names (Mary Jane Watson, Jean-Pierre)
- Various phone formats (+1, parens, dashes, dots, spaces)
- Email variations (subdomains, plus signs, unusual TLDs)
- Student ID patterns in various formats
- Name header detection
- Word boundary handling (no false positives)
"""

import pytest

from Autograder.graders.text_submission_grader import SubmissionPIIRedactor


class TestEmailRedaction:
    """Tests for email pattern detection and redaction."""

    def test_standard_email(self):
        redactor = SubmissionPIIRedactor()
        text = "Contact me at john.doe@example.com for help."
        redacted, counts = redactor.redact(text)

        assert "john.doe@example.com" not in redacted
        assert "[REDACTED_EMAIL]" in redacted
        assert counts["emails"] == 1

    def test_email_with_plus_sign(self):
        redactor = SubmissionPIIRedactor()
        text = "Email me at student+assignment@university.edu"
        redacted, counts = redactor.redact(text)

        assert "student+assignment@university.edu" not in redacted
        assert "[REDACTED_EMAIL]" in redacted
        assert counts["emails"] == 1

    def test_email_with_subdomain(self):
        redactor = SubmissionPIIRedactor()
        text = "Send to admin@cs.stanford.edu or help@mail.example.org"
        redacted, counts = redactor.redact(text)

        assert "admin@cs.stanford.edu" not in redacted
        assert "help@mail.example.org" not in redacted
        assert counts["emails"] == 2

    def test_email_with_numbers(self):
        redactor = SubmissionPIIRedactor()
        text = "Contact student2024@csumb.edu"
        redacted, counts = redactor.redact(text)

        assert "student2024@csumb.edu" not in redacted
        assert counts["emails"] == 1

    def test_email_with_unusual_tld(self):
        redactor = SubmissionPIIRedactor()
        text = "Email professor@university.education or help@school.academy"
        redacted, counts = redactor.redact(text)

        assert "professor@university.education" not in redacted
        assert "help@school.academy" not in redacted
        assert counts["emails"] == 2

    def test_multiple_emails_in_text(self):
        redactor = SubmissionPIIRedactor()
        text = "Contact alice@example.com or bob@test.org for the project."
        redacted, counts = redactor.redact(text)

        assert "alice@example.com" not in redacted
        assert "bob@test.org" not in redacted
        assert counts["emails"] == 2

    def test_email_at_end_of_sentence(self):
        redactor = SubmissionPIIRedactor()
        text = "My email is test@example.com."
        redacted, counts = redactor.redact(text)

        assert "test@example.com" not in redacted
        assert counts["emails"] == 1


class TestPhoneRedaction:
    """Tests for phone number pattern detection and redaction."""

    def test_standard_us_phone(self):
        redactor = SubmissionPIIRedactor()
        text = "Call me at 831-555-1212"
        redacted, counts = redactor.redact(text)

        assert "831-555-1212" not in redacted
        assert "[REDACTED_PHONE]" in redacted
        assert counts["phones"] == 1

    def test_phone_with_parens(self):
        redactor = SubmissionPIIRedactor()
        text = "My number is (831) 555-1212"
        redacted, counts = redactor.redact(text)

        assert "(831) 555-1212" not in redacted
        assert counts["phones"] == 1

    def test_phone_with_dots(self):
        redactor = SubmissionPIIRedactor()
        text = "Reach me at 831.555.1212"
        redacted, counts = redactor.redact(text)

        assert "831.555.1212" not in redacted
        assert counts["phones"] == 1

    def test_phone_with_spaces(self):
        redactor = SubmissionPIIRedactor()
        text = "Phone: 831 555 1212"
        redacted, counts = redactor.redact(text)

        assert "831 555 1212" not in redacted
        assert counts["phones"] == 1

    def test_phone_with_country_code(self):
        redactor = SubmissionPIIRedactor()
        text = "International: +1-831-555-1212"
        redacted, counts = redactor.redact(text)

        assert "+1-831-555-1212" not in redacted
        assert counts["phones"] == 1

    def test_phone_with_country_code_no_dash(self):
        redactor = SubmissionPIIRedactor()
        text = "Call +1 831 555 1212"
        redacted, counts = redactor.redact(text)

        assert "+1 831 555 1212" not in redacted
        assert counts["phones"] == 1

    def test_phone_mixed_formats(self):
        redactor = SubmissionPIIRedactor()
        text = "Home: (408) 555-1234, Cell: 831.555.5678"
        redacted, counts = redactor.redact(text)

        assert "(408) 555-1234" not in redacted
        assert "831.555.5678" not in redacted
        assert counts["phones"] == 2

    def test_not_a_phone_number(self):
        """Ensure we don't redact things that look like phone numbers but aren't."""
        redactor = SubmissionPIIRedactor()
        # Social security numbers have different format
        text = "Reference number 12345678 in the system."
        redacted, counts = redactor.redact(text)

        # 8 digits alone shouldn't match 10-digit phone pattern
        assert "12345678" in redacted  # Should NOT be redacted
        assert counts["phones"] == 0


class TestStudentIdRedaction:
    """Tests for student ID pattern detection and redaction."""

    def test_student_id_with_colon(self):
        redactor = SubmissionPIIRedactor()
        text = "Student ID: 12345678"
        redacted, counts = redactor.redact(text)

        assert "Student ID: 12345678" not in redacted
        assert "[REDACTED_STUDENT_ID]" in redacted
        assert counts["student_id_markers"] == 1

    def test_student_number(self):
        redactor = SubmissionPIIRedactor()
        text = "Student Number: 87654321"
        redacted, counts = redactor.redact(text)

        assert "Student Number: 87654321" not in redacted
        assert counts["student_id_markers"] == 1

    def test_sid_abbreviation(self):
        redactor = SubmissionPIIRedactor()
        text = "SID: 12345678"
        redacted, counts = redactor.redact(text)

        assert "SID: 12345678" not in redacted
        assert counts["student_id_markers"] == 1

    def test_canvas_user_id(self):
        redactor = SubmissionPIIRedactor()
        text = "canvas_user_id=98765"
        redacted, counts = redactor.redact(text)

        assert "canvas_user_id=98765" not in redacted
        assert counts["student_id_markers"] == 1

    def test_student_id_case_insensitive(self):
        redactor = SubmissionPIIRedactor()
        text = "STUDENT ID: 12345 and student id: 67890"
        redacted, counts = redactor.redact(text)

        assert "12345" not in redacted
        assert "67890" not in redacted
        assert counts["student_id_markers"] == 2

    def test_explicit_student_id_parameter(self):
        """Test that explicit student_id parameter redacts standalone ID."""
        redactor = SubmissionPIIRedactor()
        text = "I am student 98765 in this class."
        redacted, counts = redactor.redact(text, student_id=98765)

        assert "98765" not in redacted
        assert "[REDACTED_STUDENT_ID]" in redacted
        assert counts["explicit_student_id"] == 1


class TestNameHeaderRedaction:
    """Tests for name header pattern detection."""

    def test_name_colon_format(self):
        redactor = SubmissionPIIRedactor()
        text = "Name: John Smith\nTopic: Processes"
        redacted, counts = redactor.redact(text)

        assert "John Smith" not in redacted
        assert "Name: [REDACTED_NAME]" in redacted
        assert "Topic: Processes" in redacted  # Should NOT be redacted
        assert counts["name_headers"] == 1

    def test_name_header_case_insensitive(self):
        redactor = SubmissionPIIRedactor()
        text = "NAME: Alice Jones"
        redacted, counts = redactor.redact(text)

        assert "Alice Jones" not in redacted
        assert counts["name_headers"] == 1

    def test_name_header_with_spaces(self):
        redactor = SubmissionPIIRedactor()
        text = "  name :   Bob Wilson  "
        redacted, counts = redactor.redact(text)

        assert "Bob Wilson" not in redacted
        assert counts["name_headers"] == 1


class TestExplicitNameRedaction:
    """Tests for explicit student name redaction."""

    def test_simple_name(self):
        redactor = SubmissionPIIRedactor()
        text = "I think Alice Smith understood the topic well."
        redacted, counts = redactor.redact(text, student_name="Alice Smith")

        assert "Alice Smith" not in redacted
        assert "[REDACTED_NAME]" in redacted
        assert counts["explicit_student_name"] == 1

    def test_name_case_insensitive(self):
        redactor = SubmissionPIIRedactor()
        text = "JOHN DOE wrote this. john doe is a student."
        redacted, counts = redactor.redact(text, student_name="John Doe")

        assert "JOHN DOE" not in redacted
        assert "john doe" not in redacted
        assert counts["explicit_student_name"] == 2

    def test_unicode_name_accents(self):
        """Test names with accented characters."""
        redactor = SubmissionPIIRedactor()
        text = "This was written by José García about memory management."
        redacted, counts = redactor.redact(text, student_name="José García")

        assert "José García" not in redacted
        assert "[REDACTED_NAME]" in redacted
        assert counts["explicit_student_name"] == 1

    def test_unicode_name_umlaut(self):
        """Test names with umlauts."""
        redactor = SubmissionPIIRedactor()
        text = "Thomas Müller explained the concept clearly."
        redacted, counts = redactor.redact(text, student_name="Thomas Müller")

        assert "Thomas Müller" not in redacted
        assert counts["explicit_student_name"] == 1

    @pytest.mark.xfail(reason="CJK word boundary detection needs Unicode-aware pattern")
    def test_unicode_name_chinese(self):
        """Test Chinese names.

        Known limitation: The current word boundary pattern uses \\w which
        doesn't handle CJK character boundaries correctly. CJK languages
        don't use spaces between words, so word boundary detection is more
        complex. A proper fix would require Unicode word boundary support
        or language-specific tokenization.
        """
        redactor = SubmissionPIIRedactor()
        text = "李明 discussed process scheduling in detail."
        redacted, counts = redactor.redact(text, student_name="李明")

        assert "李明" not in redacted
        assert counts["explicit_student_name"] == 1

    def test_name_with_apostrophe(self):
        """Test names with apostrophes like O'Brien."""
        redactor = SubmissionPIIRedactor()
        text = "O'Brien wrote about deadlock prevention."
        redacted, counts = redactor.redact(text, student_name="O'Brien")

        assert "O'Brien" not in redacted
        assert counts["explicit_student_name"] == 1

    def test_name_oconnor_apostrophe(self):
        """Test O'Connor style names."""
        redactor = SubmissionPIIRedactor()
        text = "Mary O'Connor submitted on time."
        redacted, counts = redactor.redact(text, student_name="Mary O'Connor")

        assert "Mary O'Connor" not in redacted
        assert counts["explicit_student_name"] == 1

    def test_hyphenated_name(self):
        """Test hyphenated names."""
        redactor = SubmissionPIIRedactor()
        text = "Jean-Pierre explained the algorithm."
        redacted, counts = redactor.redact(text, student_name="Jean-Pierre")

        assert "Jean-Pierre" not in redacted
        assert counts["explicit_student_name"] == 1

    def test_multi_word_first_name(self):
        """Test names with multiple first names."""
        redactor = SubmissionPIIRedactor()
        text = "Mary Jane Watson analyzed the problem."
        redacted, counts = redactor.redact(text, student_name="Mary Jane Watson")

        assert "Mary Jane Watson" not in redacted
        assert counts["explicit_student_name"] == 1

    def test_name_with_extra_whitespace(self):
        """Test that names match even with different whitespace in text."""
        redactor = SubmissionPIIRedactor()
        text = "John   Smith wrote this."  # Extra space
        redacted, counts = redactor.redact(text, student_name="John Smith")

        assert "John   Smith" not in redacted
        assert counts["explicit_student_name"] == 1

    def test_short_name_ignored(self):
        """Names shorter than 3 chars should be ignored to avoid false positives."""
        redactor = SubmissionPIIRedactor()
        text = "I saw Bo at the lecture."
        redacted, counts = redactor.redact(text, student_name="Bo")

        # Should NOT be redacted (too short)
        assert "Bo" in redacted
        assert counts.get("explicit_student_name", 0) == 0


class TestWordBoundaryHandling:
    """Tests to ensure we don't redact partial matches inside words."""

    def test_email_not_in_word(self):
        """Emails should only match complete patterns."""
        redactor = SubmissionPIIRedactor()
        text = "The word mailto:test@example.com should match."
        redacted, counts = redactor.redact(text)

        # The email part should be redacted
        assert "test@example.com" not in redacted

    def test_name_not_substring(self):
        """Names should not match as substrings of other words."""
        redactor = SubmissionPIIRedactor()
        text = "The algorithm processes data."
        redacted, counts = redactor.redact(text, student_name="Al")

        # "Al" is too short and shouldn't match anyway
        assert "algorithm" in redacted

    def test_name_word_boundary(self):
        """Names should only match at word boundaries."""
        redactor = SubmissionPIIRedactor()
        text = "The Smalltalk language is interesting."
        redacted, counts = redactor.redact(text, student_name="Small")

        # "Small" shouldn't match inside "Smalltalk"
        # But actually "Small" is >= 3 chars, so let's test properly
        # The pattern uses (?<!\w) and (?!\w) for boundaries
        assert "Smalltalk" in redacted  # Should NOT be modified


class TestCombinedRedaction:
    """Tests for multiple types of PII in the same text."""

    def test_all_pii_types(self):
        redactor = SubmissionPIIRedactor()
        text = """
        Name: John Smith
        Email: john@example.com
        Phone: (831) 555-1212
        Student ID: 12345678

        John Smith wrote about process scheduling.
        """
        redacted, counts = redactor.redact(text, student_name="John Smith",
                                           student_id=12345678)

        assert "john@example.com" not in redacted
        assert "(831) 555-1212" not in redacted
        assert "Student ID: 12345678" not in redacted
        assert "John Smith" not in redacted

        assert counts["emails"] == 1
        assert counts["phones"] == 1
        assert counts["student_id_markers"] == 1
        assert counts["name_headers"] == 1
        assert counts["explicit_student_name"] >= 1
        assert counts["total_replacements"] >= 5


class TestEmptyAndEdgeCases:
    """Tests for empty strings and edge cases."""

    def test_empty_text(self):
        redactor = SubmissionPIIRedactor()
        redacted, counts = redactor.redact("")

        assert redacted == ""
        assert counts["total_replacements"] == 0

    def test_none_student_name(self):
        redactor = SubmissionPIIRedactor()
        text = "Normal text without PII."
        redacted, counts = redactor.redact(text, student_name=None)

        assert redacted == text
        assert counts.get("explicit_student_name", 0) == 0

    def test_none_student_id(self):
        redactor = SubmissionPIIRedactor()
        text = "Normal text without PII."
        redacted, counts = redactor.redact(text, student_id=None)

        assert redacted == text
        assert counts.get("explicit_student_id", 0) == 0

    def test_text_with_no_pii(self):
        redactor = SubmissionPIIRedactor()
        text = "This is a normal academic discussion about algorithms and data structures."
        redacted, counts = redactor.redact(text)

        assert redacted == text
        assert counts["total_replacements"] == 0
