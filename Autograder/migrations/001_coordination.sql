-- Run explicitly before enabling coordination. Safe to apply repeatedly.
CREATE TABLE IF NOT EXISTS autograder_assignment_leases (
  course_id text NOT NULL,
  assignment_id text NOT NULL,
  token text NOT NULL,
  expires_at timestamptz NOT NULL,
  PRIMARY KEY (course_id, assignment_id)
);
CREATE TABLE IF NOT EXISTS autograder_student_successes (
  course_id text NOT NULL,
  assignment_id text NOT NULL,
  student_id text NOT NULL,
  last_succeeded_at timestamptz NOT NULL,
  PRIMARY KEY (course_id, assignment_id, student_id)
);
