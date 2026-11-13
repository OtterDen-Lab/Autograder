# Shared grading coordination

Install the PostgreSQL extra on every grading machine (`uv sync --extra postgres`
for a source checkout, or install `otter-autograder[postgres]` with your deployment
tooling). Export `AUTOGRADER_DATABASE_URL` through the grader's existing protected
environment file. For example, use the production role and `autograder_prod` on
`homework-grader`, and the test role and `autograder_test` on
`homework-grader-test`, both at `db.p.zxqw.dev`. Keep passwords in the deployment
secrets rather than YAML or command-line arguments. PostgreSQL connection strings
accept the deployment's TLS settings and password-file configuration.

All workers coordinating the same Canvas installation must use the same database.
Use a separate database for another Canvas installation: course and assignment IDs
are the coordination keys. Production and test workers intentionally use different
databases and therefore do not coordinate with each other. Point test workers at
test Canvas assignments, or disable grade publishing.

Before enabling the database on workers, apply the migration using a role that can
create tables in the target schema:

```sh
python -m Autograder.coordination migrate
```

This reads the exported connection string and applies the packaged migration. It
is safe to run repeatedly. Apply it to the test database first, then production.
The runtime role needs SELECT, INSERT, UPDATE, and DELETE on
`autograder_assignment_leases`, `autograder_student_successes`, and
`autograder_panopto_checkpoints`. If a separate
migration role owns those tables, grant those privileges to the runtime role.
Workers never create tables automatically.

## Panopto incremental scans

Panopto viewer pages are requested in descending `LastViewedDateTime` order.
For ordinary, unrestricted publishing runs with PostgreSQL coordination,
successful Panopto pushes update the shared per-student success records even
when a student cooldown is not configured. The scan looks back twice the
elapsed time since the course's most recent recorded push, capped at seven
days. It also keeps at least a 15-minute overlap with the assignment's last
successful scan. The earlier of those two times is used.

Each assignment first receives a full scan, and a full scan is repeated at
least every seven days. A scan checkpoint advances only after at least one
confirmed successful Canvas push, with no publishing failures, ungraded-feedback
skips, or students deferred by cooldown. Empty scans and runs where every Canvas
update is skipped leave the checkpoint unchanged. A mixed run can record each
successful student's timestamp while retaining the scan checkpoint so deferred
students remain discoverable. Successful pushes qualify regardless of score
improvement; an update skipped before calling Canvas does not qualify. A failed, incomplete, 404, limited, test, or explicit
regrade run does not advance its checkpoint. If a checkpoint or success time
is unavailable, the scan is full. Panopto records without timestamps or a
detected ordering violation disable early termination for that scan. Sorting
is requested from Panopto; the early-stop optimization assumes the endpoint
honors it across pages. Delayed viewing updates can be picked up by the overlap
or the periodic full scan. The tool raises an error instead of silently
accepting a truncated result if it reaches its 100-page safety limit.

The course roster is fetched once per course per run. For a Canvas-backed
assignment, submission statuses are fetched for matched viewers using
`student_ids[]` and `assignment_ids[]` in batches. Existing Canvas access
permissions remain necessary. Inspect dates and pagination against your Panopto
instance before enabling this on production data. The migration above must
be rerun to create the checkpoint table before deploying this version.
If lease acquisition succeeds but the checkpoint query fails, check that this
new table exists and that the runtime role has privileges on it. Missing-table
errors use SQLSTATE `42P01`; missing columns use `42703`; insufficient privileges
use `42501`. From the source checkout, the idempotent migration can also be
applied with `psql -v ON_ERROR_STOP=1 --single-transaction -f
Autograder/migrations/001_coordination.sql` using your normal connection options.


When the connection string is set, each worker atomically claims an assignment
before preparing it. Claims last five minutes and renew every 100 seconds,
through preparation, grading, and publishing. Another worker skips a claimed
assignment and can process other assignments. The existing machine-local file
lock remains in place. A crashed worker's claim becomes available after expiry.
Database errors fail the assignment; they do not permit uncoordinated grading.
When the connection string is absent, existing grading behavior remains available,
but configuring a cooldown without a database causes the assignment to fail.

## Per-student cooldown

Set `grading_cooldown` in seconds in assignment-type settings, or override it in
course, group, or assignment settings using the existing settings inheritance:

```yaml
assignment_types:
  programming:
    kind: ProgrammingAssignment
    grader: template-grader
    settings:
      grading_cooldown: 300
      # Include the normal programming grader settings here.
```

After pulling the latest submissions, the worker excludes students whose last
successful Canvas update falls within the cooldown. Text batch inputs are filtered
along with the submission list. A changed submission still waits for the cooldown;
no submission-version tracking is added. Each successful Canvas update advances
only that student's timestamp, so partial publishing failures leave other students
eligible for retry. Failed grading, failed publishing, preview runs, and skipped
Canvas updates do not advance the timestamp. A zero cooldown tracks success without
waiting. Omit the setting (or override with null) to disable timestamp reads and
writes, while retaining assignment locking. This is suitable for Panopto.
Existing regrade/status/score filters continue to apply; a cooldown does not itself
make an already-graded submission eligible for regrading.

Use a regular external scan timer and remove the assignment type's `schedule`
block when replacing frequent RRULE schedules with cooldowns. Otherwise the old
schedule check still runs before coordination, including its enumeration cost.
Calendar schedules remain available and continue to limit when assignments run.
Panopto's schedule-derived stale-watch cutoff also requires a retained schedule;
without one, the existing fallback behavior applies.

## Failure and recovery

Workers check lease ownership before grading, before publishing, and before each
student's Canvas mutations. Expired owners cannot renew, record success, or release
a successor's lease. If renewal fails, the worker refuses subsequent publishing;
in-progress grading may finish before that check. Lease loss does not cancel an
already-running Docker job or Canvas request.

Canvas does not participate in the PostgreSQL transaction. A process paused
between an ownership check and a Canvas request may still send that request after
its lease expires. Similarly, a crash after Canvas accepts an update but before
PostgreSQL records success can cause a retry. This provides practical coordination,
not exactly-once Canvas writes. Existing Canvas score/idempotency protections still
apply. Deploy all workers with coordination enabled before allowing overlap; an
older worker that does not claim leases cannot be protected by the database.

## Tests

Run unit and pipeline tests with `pytest`. For real database race/recovery tests,
export `AUTOGRADER_TEST_DATABASE_URL` pointing to a disposable PostgreSQL database
and run `pytest tests/test_coordination.py`. Tests create the tables, use unique
course keys, and remove their own rows. No production credentials are required.
