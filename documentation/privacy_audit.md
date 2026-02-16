# Privacy Audit

This document tracks where student-identifying data can flow and how it is controlled.

## Scope

Audit date: 2026-02-14  
Search terms: `student.name`, `user_id`, `student_id`, `privacy_mode`, `reveal_identity`

## High-Risk Surfaces

1. LMS ingestion (`lms_interface/canvas_interface.py`)
- Raw names and Canvas IDs originate here.
- Control: `PrivacyContext` applies `privacy_mode` centrally (`none|id_only|blind`).
- Blind mode labels are persisted across runs in `~/.autograder/privacy/blind_id_map.json` (override: `AUTOGRADER_BLIND_ID_MAP_PATH`).

2. Submission text sent to LLM (`Autograder/graders/text_submission_grader.py`)
- Student submissions may contain names/emails/IDs.
- Control: `SubmissionPIIRedactor` redacts common patterns before Phase 1/2 model calls.
- Redaction events are tracked in grader report data (`privacy_summary`).

3. Run logs and break-glass identity reveal (`Autograder/grade_assignments.py`)
- Logs can include Canvas IDs when reveal mode is enabled.
- Control: reveal requires `AUTOGRADER_BREAK_GLASS=1`.
- Control: reveal events are written to audit log (`~/.autograder/privacy/reveal_identity_audit.log`, override `AUTOGRADER_REVEAL_AUDIT_LOG`).

4. File attachments and filenames (`lms_interface/classes.py`)
- Canvas attachment filenames may include PII.
- Control: filename sanitization (`FileSubmission__Canvas._sanitize_filename`) + path traversal protection + MIME/type validation.

## Known Remaining Risks

- Free-form instructor-authored comments and custom grader logs can still include names if intentionally added.
- PII redaction is best-effort regex-based and may miss uncommon formats.

## Operational Guidance

1. Default to `privacy_mode: blind` for routine grading.
2. Use reveal mode only for debugging and keep break-glass windows short.
3. Retain and review reveal audit logs periodically.
4. Keep records directories outside the repo and limit access permissions.
