# Web Grading Interface - Deployment Roadmap

**Document Version:** 1.0
**Last Updated:** 2025-10-14
**Status:** Planning Phase

## Overview

This document outlines the deployment strategy for the Web Grading Interface, focusing on packaging for distribution, LTI integration with Canvas, FERPA compliance, and production deployment.

### Strategic Goals

1. **Enable Local Deployment** - Instructors can run on personal machines via Docker
2. **Canvas Integration** - Seamless LTI 1.3 integration for institutional deployment
3. **Data Privacy** - FERPA-compliant data handling and anonymization
4. **Scalable Architecture** - Support single-user to multi-instructor deployments

### Timeline Overview

| Phase | Duration | Status |
|-------|----------|--------|
| Phase 1: Docker Packaging | 1-2 weeks | 🔲 Not Started |
| Phase 2: Documentation & Demo | 1 week | 🔲 Not Started |
| Phase 3: LTI 1.3 Integration | 2-3 weeks | 🔲 Not Started |
| Phase 4: FERPA Compliance | 1-2 weeks | 🔲 Not Started |
| Phase 5: Beta Deployment | 2-3 weeks | 🔲 Not Started |
| Phase 6: Production Release | 1 week | 🔲 Not Started |

**Total Estimated Timeline:** 8-12 weeks

---

## Phase 1: Docker Packaging (Weeks 1-2)

**Goal:** Package the web grading interface as a Docker container for easy local deployment

### 1.1 Create Dockerfile

**Location:** `/Users/ssogden/repos/teaching/Autograder/docker/web-grading/Dockerfile`

**Tasks:**
- [ ] Create Dockerfile with Python 3.12 base image
- [ ] Install system dependencies (git, build tools)
- [ ] Handle LMSInterface path dependency (copy into build context)
- [ ] Install Python dependencies via uv
- [ ] Copy web_grading application code
- [ ] Set up working directory and expose port 8000
- [ ] Add health check endpoint verification
- [ ] Optimize image size (multi-stage build if needed)

**Technical Specifications:**
```dockerfile
# Key requirements:
- Base: python:3.12-slim
- Port: 8000
- Health check: /api/health
- Volume mount: /data (for database persistence)
- Environment variables: Canvas API keys, AI API keys
```

**Success Criteria:**
- [ ] Docker image builds without errors
- [ ] Container starts and health check passes
- [ ] Can access web interface at http://localhost:8000
- [ ] Database persists across container restarts
- [ ] Image size < 1GB

### 1.2 Create Docker Compose Configuration

**Location:** `/Users/ssogden/repos/teaching/Autograder/docker/web-grading/docker-compose.yml`

**Tasks:**
- [ ] Create docker-compose.yml for orchestration
- [ ] Configure volume mounts for data persistence
- [ ] Set up environment variable loading (.env file)
- [ ] Add restart policies
- [ ] Configure networking (if multi-container in future)
- [ ] Add comments/documentation in compose file

**Technical Specifications:**
```yaml
# Key components:
services:
  web:
    - Port mapping: 8000:8000
    - Volume: grading-data:/data
    - Environment variables from .env
    - Restart: unless-stopped
```

**Success Criteria:**
- [ ] `docker-compose up -d` starts services
- [ ] `docker-compose ps` shows healthy status
- [ ] Persistent data survives `docker-compose down && docker-compose up`
- [ ] Environment variables correctly loaded

### 1.3 Handle Path Dependencies

**Issue:** LMSInterface is currently a path dependency at `../LMSInterface`

**Options:**
1. Copy LMSInterface into Docker build context
2. Publish lms-interface as private PyPI package
3. Use Git submodule

**Tasks:**
- [ ] Decide on approach (recommend Option 1 for simplicity)
- [ ] Update Dockerfile to copy LMSInterface
- [ ] Modify pyproject.toml path during build if needed
- [ ] Test that imports work correctly in container
- [ ] Document the approach for future maintainers

**Success Criteria:**
- [ ] `from lms_interface import CanvasInterface` works in container
- [ ] No path-related import errors

### 1.4 Environment Configuration

**Location:** `/Users/ssogden/repos/teaching/Autograder/docker/web-grading/.env.example`

**Tasks:**
- [ ] Create .env.example template file
- [ ] Document all required environment variables
- [ ] Add .env to .gitignore (if not already)
- [ ] Create environment variable validation on startup
- [ ] Add default values where appropriate

**Required Variables:**
```bash
# Canvas API
CANVAS_API_KEY_DEV=
CANVAS_API_KEY_PROD=
CANVAS_API_URL_DEV=https://canvas.beta.instructure.com
CANVAS_API_URL_PROD=https://csumb.instructure.com

# AI Services
ANTHROPIC_API_KEY=
OPENAI_API_KEY=

# Application
DATABASE_PATH=/data/grading.db
SECRET_KEY=  # For future JWT signing
LOG_LEVEL=INFO

# Optional
SLACK_WEBHOOK=
```

**Success Criteria:**
- [ ] Missing required variables cause clear error messages
- [ ] .env.example is complete and documented
- [ ] Users can copy .env.example to .env and fill in values

### 1.5 Testing & Documentation

**Tasks:**
- [ ] Test clean build (no caching)
- [ ] Test on fresh machine (VM or colleague's computer)
- [ ] Create README.md with deployment instructions
- [ ] Document port requirements and firewall notes
- [ ] Create troubleshooting section
- [ ] Test startup scripts

**Documentation Location:** `/Users/ssogden/repos/teaching/Autograder/docker/web-grading/README.md`

**Success Criteria:**
- [ ] Non-technical user can deploy following README
- [ ] All common issues documented in troubleshooting
- [ ] Tested on macOS and Linux

### 1.6 Deliverables Checklist

- [ ] Dockerfile (tested and optimized)
- [ ] docker-compose.yml (fully configured)
- [ ] .env.example (complete with documentation)
- [ ] README.md (deployment guide)
- [ ] .dockerignore (excludes unnecessary files)
- [ ] Deployment tested on 2+ machines

---

## Phase 2: Documentation & Demo (Week 3)

**Goal:** Create materials for IT approval and user onboarding

### 2.1 User Documentation

**Location:** `/Users/ssogden/repos/teaching/Autograder/Autograder/web_grading/docs/user-guide.md`

**Tasks:**
- [ ] Write user guide covering:
  - [ ] Creating a grading session
  - [ ] Uploading exam PDFs
  - [ ] Name matching workflow
  - [ ] Problem-by-problem grading
  - [ ] Using AI-assisted features
  - [ ] Finalizing and uploading to Canvas
- [ ] Include screenshots for each major step
- [ ] Document keyboard shortcuts
- [ ] Add FAQ section

**Success Criteria:**
- [ ] New user can complete full grading workflow using only docs
- [ ] Screenshots are clear and up-to-date

### 2.2 Installation Guide

**Location:** `/Users/ssogden/repos/teaching/Autograder/Autograder/web_grading/docs/installation.md`

**Tasks:**
- [ ] Write installation guide for:
  - [ ] Local Docker deployment
  - [ ] System requirements
  - [ ] Obtaining API keys
  - [ ] Configuration walkthrough
  - [ ] First-time setup
- [ ] Include platform-specific instructions (macOS, Linux, Windows)
- [ ] Add uninstallation/cleanup instructions

**Success Criteria:**
- [ ] 3 colleagues successfully install without assistance
- [ ] Common errors documented with solutions

### 2.3 Demo Video/Screenshots

**Tasks:**
- [ ] Record demo video (5-10 minutes) showing:
  - [ ] Complete grading workflow
  - [ ] Key features (AI assist, blank detection, etc.)
  - [ ] Finalization and Canvas integration
- [ ] Take high-quality screenshots for documentation
- [ ] Create animated GIFs for common workflows
- [ ] Upload to accessible location (YouTube unlisted, or institutional storage)

**Tools:** Loom, QuickTime, OBS Studio

**Success Criteria:**
- [ ] Video clearly demonstrates value proposition
- [ ] Audio is clear and professional
- [ ] Covers all major features in < 10 minutes

### 2.4 Security & Privacy Documentation

**Location:** `/Users/ssogden/repos/teaching/Autograder/Autograder/web_grading/docs/security-privacy.md`

**Tasks:**
- [ ] Document data handling practices
- [ ] List data stored and retention periods
- [ ] Explain security measures (encryption, access control)
- [ ] Describe FERPA compliance approach
- [ ] Document third-party services (Anthropic, OpenAI)
- [ ] Create privacy policy for end users
- [ ] Write data processing agreement template for institutions

**Success Criteria:**
- [ ] Addresses common IT security questionnaire items
- [ ] Clear explanation of FERPA compliance
- [ ] Legal language reviewed (if possible)

### 2.5 IT Approval Package

**Location:** `/Users/ssogden/repos/teaching/Autograder/Autograder/web_grading/docs/it-approval-package.md`

**Tasks:**
- [ ] Create package containing:
  - [ ] Executive summary (1 page)
  - [ ] Feature overview
  - [ ] Security questionnaire responses
  - [ ] LTI configuration specification
  - [ ] Hosting requirements
  - [ ] Support plan
  - [ ] Privacy policy
  - [ ] Demo video link
- [ ] Tailor to institution's approval process

**Success Criteria:**
- [ ] IT has all information needed to evaluate
- [ ] Addresses security, privacy, and technical concerns
- [ ] Professional presentation

### 2.6 Deliverables Checklist

- [ ] User guide (comprehensive)
- [ ] Installation guide (tested)
- [ ] Demo video (< 10 min, professional)
- [ ] Security documentation (FERPA-focused)
- [ ] IT approval package (complete)
- [ ] Screenshots and GIFs (clear, up-to-date)

---

## Phase 3: LTI 1.3 Integration (Weeks 4-6)

**Goal:** Implement LTI 1.3 protocol for seamless Canvas integration

### 3.1 LTI Library Setup

**Tasks:**
- [ ] Research LTI libraries (pylti1p3 recommended)
- [ ] Add pylti1p3 to dependencies
- [ ] Create LTI configuration structure
- [ ] Set up key storage for LTI credentials

**Dependencies to add:**
```toml
# In pyproject.toml
dependencies = [
    # ... existing ...
    "pylti1p3>=2.0.0",
    "pyjwt>=2.8.0",
    "cryptography>=41.0.0",
]
```

**Success Criteria:**
- [ ] pylti1p3 imports successfully
- [ ] Configuration loaded correctly

### 3.2 LTI Endpoints Implementation

**Location:** `/Users/ssogden/repos/teaching/Autograder/Autograder/web_grading/web_api/lti/`

**Tasks:**
- [ ] Create LTI module structure:
  ```
  web_api/lti/
    __init__.py
    config.py          # LTI configuration
    handlers.py        # Launch, login handlers
    middleware.py      # LTI session middleware
    validators.py      # JWT validation
    models.py          # LTI data models
  ```
- [ ] Implement `/lti/login` (OIDC initiation)
- [ ] Implement `/lti/launch` (LTI resource link)
- [ ] Implement `/lti/jwks` (public key endpoint)
- [ ] Add LTI routes to main.py

**Technical Specifications:**
```python
# Key endpoints:
@router.post("/lti/login")
async def lti_login():
    # OIDC login initiation
    # Redirect to Canvas authorization endpoint

@router.post("/lti/launch")
async def lti_launch():
    # Verify JWT signature
    # Extract user_id, course_id, assignment_id, roles
    # Create/retrieve session
    # Redirect to grading interface with context

@router.get("/lti/jwks")
async def jwks():
    # Return public key in JWKS format
    # For Canvas to verify our signatures
```

**Success Criteria:**
- [ ] Endpoints accept POST requests
- [ ] JWT validation works with test data
- [ ] Returns proper HTTP status codes and errors

### 3.3 LTI Configuration Management

**Location:** `/Users/ssogden/repos/teaching/Autograder/Autograder/web_grading/web_api/lti/config.py`

**Tasks:**
- [ ] Create LTI configuration dataclass
- [ ] Support multiple Canvas instances (dev/prod)
- [ ] Store deployment IDs and client IDs
- [ ] Implement key rotation support
- [ ] Add configuration validation

**Configuration Structure:**
```python
@dataclass
class LTIConfig:
    issuer: str  # Canvas URL
    client_id: str
    deployment_id: str
    auth_login_url: str
    auth_token_url: str
    key_set_url: str
    # ... additional fields
```

**Success Criteria:**
- [ ] Supports multiple Canvas environments
- [ ] Configuration validated on load
- [ ] Clear error messages for misconfigurations

### 3.4 Mock LTI Mode for Development

**Location:** `/Users/ssogden/repos/teaching/Autograder/Autograder/web_grading/web_api/lti/mock.py`

**Tasks:**
- [ ] Create mock LTI launch generator
- [ ] Support different user roles (Instructor, TA, Student)
- [ ] Generate valid JWTs with test keys
- [ ] Add dev-mode bypass for local testing
- [ ] Document usage in README

**Mock Launch Features:**
```python
# Development endpoints:
@router.get("/lti/dev-launch")  # Quick test launch
@router.get("/lti/dev-launch-as/{role}")  # Test different roles

# Mock data generator:
def create_mock_lti_launch(
    user_id="12345",
    course_id=29978,
    assignment_id=506889,
    role="Instructor"
)
```

**Success Criteria:**
- [ ] Can test LTI flow without Canvas
- [ ] Mock data matches real LTI payload structure
- [ ] Dev mode documented clearly

### 3.5 Context Extraction & Session Management

**Tasks:**
- [ ] Extract course_id, assignment_id from LTI claims
- [ ] Extract user identity (Canvas user_id, name)
- [ ] Parse user roles (Instructor, TA, Student)
- [ ] Create session with LTI context
- [ ] Store LTI context in database
- [ ] Link grading sessions to LTI launches

**Database Changes:**
```sql
-- Add LTI context to sessions
ALTER TABLE grading_sessions ADD COLUMN lti_context TEXT;
ALTER TABLE grading_sessions ADD COLUMN lti_user_id TEXT;
ALTER TABLE grading_sessions ADD COLUMN lti_roles TEXT;
```

**Success Criteria:**
- [ ] Course/assignment context pre-populated
- [ ] User identity stored securely
- [ ] Session linked to LTI launch

### 3.6 Grade Passback Implementation

**Location:** `/Users/ssogden/repos/teaching/Autograder/Autograder/web_grading/web_api/lti/grade_passback.py`

**Tasks:**
- [ ] Implement LTI Advantage Assignment and Grade Services (AGS)
- [ ] Create grade submission endpoint
- [ ] Handle score formatting (0.0-1.0 vs 0-100)
- [ ] Add error handling and retries
- [ ] Test with Canvas beta

**Grade Passback Flow:**
```python
async def submit_grades_via_lti(session_id: int):
    # Get LTI lineitem URL from launch
    # For each student:
    #   - Calculate final score
    #   - Format as LTI score (0.0-1.0)
    #   - POST to lineitem score endpoint
    #   - Handle errors
```

**Success Criteria:**
- [ ] Grades successfully posted to Canvas
- [ ] Handles partial failures gracefully
- [ ] Errors logged for debugging

### 3.7 LTI Deep Linking (Optional)

**Purpose:** Allow instructors to "install" the tool directly from Canvas

**Tasks:**
- [ ] Implement deep linking request handler
- [ ] Return content items (tool configuration)
- [ ] Test installation flow in Canvas

**Note:** Can be deferred to Phase 5

### 3.8 Testing & Integration

**Tasks:**
- [ ] Unit tests for JWT validation
- [ ] Integration tests with mock LTI launches
- [ ] Test with Canvas LTI simulator (https://lti.tools/test)
- [ ] Test with Canvas free-for-teachers account
- [ ] Document testing procedure

**Success Criteria:**
- [ ] All LTI endpoints have test coverage
- [ ] Mock launches work end-to-end
- [ ] Tested with real Canvas (beta)

### 3.9 Deliverables Checklist

- [ ] LTI module (handlers, validators, config)
- [ ] Mock LTI mode (for development)
- [ ] Grade passback (AGS implementation)
- [ ] Database schema updates
- [ ] Unit and integration tests
- [ ] LTI documentation

---

## Phase 4: FERPA Compliance (Weeks 7-8)

**Goal:** Implement data privacy and security measures for FERPA compliance

### 4.1 Data Anonymization

**Tasks:**
- [ ] Add per-session salt generation
- [ ] Implement student name hashing (HMAC-SHA256)
- [ ] Create anonymized display names ("Student #42")
- [ ] Add "reveal names" mode (requires re-auth)
- [ ] Encrypt Canvas user IDs at rest
- [ ] Update UI to show anonymized names by default

**Database Changes:**
```sql
ALTER TABLE grading_sessions ADD COLUMN anonymization_salt TEXT;
ALTER TABLE submissions ADD COLUMN student_id_hash TEXT;
ALTER TABLE submissions ADD COLUMN encrypted_canvas_id TEXT;
ALTER TABLE grading_sessions ADD COLUMN anonymization_enabled INTEGER DEFAULT 1;
```

**Anonymization Algorithm:**
```python
def anonymize_student_name(name: str, session_salt: str) -> str:
    # HMAC-SHA256(name + session_salt)
    # Truncate to 8 chars for display: "Student #a3f2b1c8"

def encrypt_canvas_id(user_id: int, session_key: str) -> str:
    # AES encryption with per-session key
```

**Success Criteria:**
- [ ] Student names not visible in database
- [ ] Names only revealed during finalization
- [ ] Hashing consistent within session
- [ ] UI updated to show anonymized names

### 4.2 Access Control & Authentication

**Tasks:**
- [ ] Add user authentication (LTI provides this)
- [ ] Implement session ownership (user_id)
- [ ] Add row-level security to database queries
- [ ] Prevent cross-user session access
- [ ] Add admin role support (optional)

**Database Changes:**
```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    canvas_user_id INTEGER NOT NULL,
    canvas_instance TEXT NOT NULL,
    email_hash TEXT,
    role TEXT DEFAULT 'instructor',
    created_at TIMESTAMP,
    last_login TIMESTAMP,
    UNIQUE(canvas_user_id, canvas_instance)
);

ALTER TABLE grading_sessions ADD COLUMN owner_user_id INTEGER;
ALTER TABLE grading_sessions ADD COLUMN shared_with TEXT;  -- JSON array
```

**Success Criteria:**
- [ ] Users only see their own sessions
- [ ] Unauthorized access returns 403
- [ ] Session sharing works (if implemented)

### 4.3 Audit Logging

**Location:** `/Users/ssogden/repos/teaching/Autograder/Autograder/web_grading/web_api/audit.py`

**Tasks:**
- [ ] Create audit log system
- [ ] Log security-relevant events:
  - [ ] User login/logout
  - [ ] Session creation/deletion
  - [ ] Grade modifications
  - [ ] Finalization (grade upload to Canvas)
  - [ ] Data exports
  - [ ] Failed authentication attempts
- [ ] Store logs separately from application DB
- [ ] Implement log rotation
- [ ] Add log search/filtering

**Audit Log Schema:**
```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_id INTEGER,
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id INTEGER,
    details TEXT,  -- JSON
    ip_address TEXT,
    user_agent TEXT
);
CREATE INDEX idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX idx_audit_user ON audit_log(user_id);
```

**Success Criteria:**
- [ ] All sensitive actions logged
- [ ] Logs immutable (no deletion via app)
- [ ] Searchable by user, date, action
- [ ] Retention policy implemented

### 4.4 Data Retention & Deletion

**Tasks:**
- [ ] Define retention policy (recommend 90 days after finalization)
- [ ] Implement automatic cleanup job
- [ ] Add manual deletion endpoint (with audit log)
- [ ] Implement "purge student data" feature
- [ ] Add warning before session deletion
- [ ] Create data export before deletion

**Retention Policy:**
```python
# Recommended retention periods:
- Active sessions: Indefinite
- Finalized sessions: 90 days
- Exported sessions: User responsibility
- Audit logs: 1 year
```

**Success Criteria:**
- [ ] Old sessions automatically cleaned up
- [ ] Users warned before deletion
- [ ] Deletion logged in audit trail
- [ ] No orphaned data in database

### 4.5 Encryption at Rest

**Tasks:**
- [ ] Evaluate SQLCipher for database encryption
- [ ] Encrypt sensitive fields (names, images)
- [ ] Secure key management strategy
- [ ] Document encryption approach
- [ ] Test performance impact

**Note:** This may be deferred if deploying locally only

**Success Criteria:**
- [ ] Database encrypted on disk
- [ ] Keys stored securely (not in code)
- [ ] Minimal performance impact

### 4.6 Privacy Policy & Terms of Service

**Location:** `/Users/ssogden/repos/teaching/Autograder/Autograder/web_grading/docs/privacy-policy.md`

**Tasks:**
- [ ] Write privacy policy covering:
  - [ ] What data is collected
  - [ ] How data is used
  - [ ] Who has access
  - [ ] Retention periods
  - [ ] User rights (access, deletion)
  - [ ] Third-party services (Anthropic, OpenAI)
  - [ ] FERPA compliance statement
- [ ] Create terms of service
- [ ] Add privacy policy to UI (link in footer)
- [ ] Require acceptance on first use (optional)

**Success Criteria:**
- [ ] Privacy policy covers all FERPA requirements
- [ ] Written in clear, accessible language
- [ ] Reviewed by legal (if available)

### 4.7 Third-Party Service Agreements

**Tasks:**
- [ ] Review Anthropic/OpenAI terms for FERPA compliance
- [ ] Document data sent to AI services
- [ ] Implement data minimization (don't send names to AI)
- [ ] Add option to disable AI features
- [ ] Create data processing addendum

**AI Service Data Handling:**
```python
# Ensure AI prompts don't include student names:
def get_ai_grading_prompt(problem_image: str, rubric: str):
    # Use anonymized IDs only
    # Don't send: "Grade John Doe's answer"
    # Send: "Grade this answer: [image]"
```

**Success Criteria:**
- [ ] No PII sent to AI services
- [ ] AI usage documented
- [ ] Users can opt out

### 4.8 Security Testing

**Tasks:**
- [ ] Perform security audit of code
- [ ] Test for common vulnerabilities:
  - [ ] SQL injection (use parameterized queries)
  - [ ] XSS (sanitize user input)
  - [ ] CSRF (use tokens)
  - [ ] Authentication bypass
  - [ ] Authorization bypass
  - [ ] Session hijacking
- [ ] Run automated security scanner (e.g., Bandit)
- [ ] Document findings and fixes

**Success Criteria:**
- [ ] No high-severity vulnerabilities
- [ ] Security best practices followed
- [ ] Findings documented

### 4.9 Deliverables Checklist

- [ ] Data anonymization (implemented and tested)
- [ ] Access control (user isolation)
- [ ] Audit logging (comprehensive)
- [ ] Data retention policy (automated)
- [ ] Privacy policy (FERPA-compliant)
- [ ] Security testing (completed)

---

## Phase 5: Beta Deployment (Weeks 9-10)

**Goal:** Deploy to test environment and conduct pilot testing

### 5.1 Beta Server Setup

**Tasks:**
- [ ] Choose hosting platform:
  - [ ] Option A: Campus hosting (recommended if available)
  - [ ] Option B: Cloud VPS (DigitalOcean, Linode, AWS Lightsail)
  - [ ] Option C: Personal server
- [ ] Provision server (minimum: 2 CPU, 4GB RAM, 50GB storage)
- [ ] Install Docker and docker-compose
- [ ] Configure firewall (allow 80, 443, 22)
- [ ] Set up domain name (grading-beta.yourschool.edu)
- [ ] Install SSL certificate (Let's Encrypt)

**Success Criteria:**
- [ ] Server accessible via HTTPS
- [ ] Docker containers running
- [ ] Health check endpoint responds

### 5.2 SSL/TLS Configuration

**Tasks:**
- [ ] Install certbot for Let's Encrypt
- [ ] Obtain SSL certificate
- [ ] Configure nginx as reverse proxy
- [ ] Set up automatic certificate renewal
- [ ] Test HTTPS access

**Nginx Configuration:**
```nginx
server {
    listen 443 ssl;
    server_name grading-beta.yourschool.edu;

    ssl_certificate /etc/letsencrypt/live/grading-beta.yourschool.edu/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/grading-beta.yourschool.edu/privkey.pem;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

**Success Criteria:**
- [ ] HTTPS works with valid certificate
- [ ] HTTP redirects to HTTPS
- [ ] SSL Labs test: A rating or better

### 5.3 Canvas Beta Integration

**Tasks:**
- [ ] Submit LTI developer key request to Canvas admin
- [ ] Provide LTI configuration JSON:
  ```json
  {
    "title": "Exam Grading Interface (BETA)",
    "description": "Web-based tool for grading scanned exams",
    "target_link_uri": "https://grading-beta.yourschool.edu/lti/launch",
    "oidc_initiation_url": "https://grading-beta.yourschool.edu/lti/login",
    "public_jwk_url": "https://grading-beta.yourschool.edu/lti/jwks",
    "scopes": [
      "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem",
      "https://purl.imsglobal.org/spec/lti-ags/scope/score"
    ]
  }
  ```
- [ ] Receive client_id and deployment_id from admin
- [ ] Configure app with Canvas credentials
- [ ] Test LTI launch from Canvas beta

**Success Criteria:**
- [ ] LTI launch works from Canvas
- [ ] Context (course/assignment) extracted correctly
- [ ] Grade passback succeeds

### 5.4 Pilot Testing

**Tasks:**
- [ ] Recruit 2-3 pilot instructors
- [ ] Create test course in Canvas beta
- [ ] Provide pilot users with:
  - [ ] Access instructions
  - [ ] User guide
  - [ ] Support contact
- [ ] Monitor usage and errors
- [ ] Collect feedback via:
  - [ ] Survey
  - [ ] Interviews
  - [ ] Issue tracker
- [ ] Track metrics:
  - [ ] Number of grading sessions
  - [ ] Problems graded
  - [ ] Error rates
  - [ ] Performance issues

**Success Criteria:**
- [ ] 3+ grading sessions completed successfully
- [ ] Grades submitted to Canvas correctly
- [ ] No data loss or corruption
- [ ] Positive feedback from pilots

### 5.5 Monitoring & Logging

**Tasks:**
- [ ] Set up application logging (log level, rotation)
- [ ] Configure server monitoring:
  - [ ] CPU/memory usage
  - [ ] Disk space
  - [ ] Network traffic
- [ ] Set up error tracking (e.g., Sentry, or email alerts)
- [ ] Create monitoring dashboard
- [ ] Set up uptime monitoring (e.g., UptimeRobot)

**Success Criteria:**
- [ ] Logs accessible and searchable
- [ ] Alerts configured for errors
- [ ] Monitoring dashboard functional

### 5.6 Bug Fixes & Iteration

**Tasks:**
- [ ] Triage issues from pilot testing
- [ ] Fix critical bugs
- [ ] Improve performance bottlenecks
- [ ] Update documentation based on feedback
- [ ] Deploy fixes to beta server

**Success Criteria:**
- [ ] All critical bugs fixed
- [ ] Performance acceptable (< 2s page load)
- [ ] Pilots report improved experience

### 5.7 Security Hardening

**Tasks:**
- [ ] Review server security:
  - [ ] Disable SSH password auth (keys only)
  - [ ] Configure fail2ban
  - [ ] Enable automatic security updates
  - [ ] Minimize attack surface (close unused ports)
- [ ] Review application security:
  - [ ] Update dependencies
  - [ ] Fix any security findings
  - [ ] Test rate limiting
- [ ] Create incident response plan

**Success Criteria:**
- [ ] Server passes security audit
- [ ] No known vulnerabilities
- [ ] Incident plan documented

### 5.8 Deliverables Checklist

- [ ] Beta server (running and monitored)
- [ ] SSL certificate (valid and auto-renewing)
- [ ] Canvas beta integration (working)
- [ ] Pilot testing (completed with feedback)
- [ ] Monitoring (logs, alerts, uptime)
- [ ] Bug fixes (critical issues resolved)

---

## Phase 6: Production Release (Week 11+)

**Goal:** Deploy to production Canvas and make available to all instructors

### 6.1 Production Infrastructure

**Tasks:**
- [ ] Decide on production hosting (campus vs. cloud)
- [ ] Provision production server (scaled for load)
- [ ] Set up production domain (grading.yourschool.edu)
- [ ] Configure production SSL certificate
- [ ] Mirror beta configuration with production values
- [ ] Set up automated backups:
  - [ ] Database backups (daily)
  - [ ] File backups (if storing PDFs on disk)
  - [ ] Test restore process

**Success Criteria:**
- [ ] Production server fully configured
- [ ] Backups tested and verified
- [ ] High availability (99.9% uptime target)

### 6.2 Canvas Production Integration

**Tasks:**
- [ ] Submit production LTI developer key request
- [ ] Update configuration with production URLs
- [ ] Configure production Canvas credentials
- [ ] Test LTI launch in production Canvas
- [ ] Test grade passback in production

**Success Criteria:**
- [ ] Production LTI integration working
- [ ] Grades correctly submitted to production Canvas
- [ ] No beta/test data in production

### 6.3 Rollout Strategy

**Tasks:**
- [ ] Define rollout plan:
  - [ ] Week 1: Pilot instructors only
  - [ ] Week 2-3: Early adopters (opt-in)
  - [ ] Week 4+: General availability
- [ ] Create announcement for instructors
- [ ] Set up support channel (email, Slack, etc.)
- [ ] Create onboarding materials
- [ ] Schedule training sessions (optional)

**Success Criteria:**
- [ ] Rollout plan documented
- [ ] Instructors informed
- [ ] Support channel ready

### 6.4 Production Monitoring

**Tasks:**
- [ ] Enhance monitoring for production:
  - [ ] Real-time error alerts
  - [ ] Performance metrics (response times)
  - [ ] Usage analytics (sessions, problems graded)
  - [ ] Resource utilization (scaling decisions)
- [ ] Set up on-call rotation (if multi-person team)
- [ ] Create runbook for common issues

**Success Criteria:**
- [ ] Monitoring more comprehensive than beta
- [ ] Alerts configured for critical issues
- [ ] Runbook covers common scenarios

### 6.5 Documentation Finalization

**Tasks:**
- [ ] Update all documentation for production:
  - [ ] User guide
  - [ ] Admin guide
  - [ ] Support documentation
  - [ ] API documentation (if applicable)
- [ ] Create changelog
- [ ] Write release notes
- [ ] Document known limitations

**Success Criteria:**
- [ ] Documentation accurate and complete
- [ ] Release notes published
- [ ] Known issues documented

### 6.6 Training & Support

**Tasks:**
- [ ] Offer training sessions for instructors
- [ ] Create video tutorials
- [ ] Set up support ticketing system (or use existing)
- [ ] Define SLA for support responses
- [ ] Create instructor FAQ

**Success Criteria:**
- [ ] Training materials available
- [ ] Support process established
- [ ] Response time expectations set

### 6.7 Legal & Compliance

**Tasks:**
- [ ] Finalize privacy policy
- [ ] Get legal review (if required)
- [ ] Sign data processing agreement with institution
- [ ] Document FERPA compliance measures
- [ ] Ensure terms of service accepted by users

**Success Criteria:**
- [ ] Legal requirements met
- [ ] Compliance documented
- [ ] Institutional approval obtained

### 6.8 Launch Announcement

**Tasks:**
- [ ] Prepare launch announcement
- [ ] Notify all instructors
- [ ] Post to institutional teaching resources page
- [ ] Share in relevant channels (LMS newsletter, etc.)
- [ ] Celebrate! 🎉

**Success Criteria:**
- [ ] Instructors aware of new tool
- [ ] Clear instructions for getting started
- [ ] Support contact visible

### 6.9 Post-Launch Review

**Tasks (2-4 weeks after launch):**
- [ ] Review usage metrics
- [ ] Collect user feedback
- [ ] Analyze support tickets
- [ ] Identify improvement areas
- [ ] Plan next iteration

**Success Criteria:**
- [ ] Adoption metrics tracked
- [ ] User satisfaction measured
- [ ] Roadmap for improvements

### 6.10 Deliverables Checklist

- [ ] Production deployment (stable and monitored)
- [ ] Canvas production integration (verified)
- [ ] Rollout completed (phased approach)
- [ ] Documentation finalized (up-to-date)
- [ ] Training and support (available)
- [ ] Legal compliance (confirmed)
- [ ] Launch announcement (distributed)

---

## Success Metrics

### Phase 1-2: Development & Documentation
- Docker image builds and runs successfully
- Documentation complete and tested by 3+ users
- Demo video clearly communicates value

### Phase 3-4: LTI & FERPA
- LTI launches work from Canvas
- Grade passback successful
- Data properly anonymized
- Security audit clean

### Phase 5: Beta Testing
- 3+ pilot instructors complete full grading workflows
- No critical bugs or data loss
- Positive feedback (4/5 or better)

### Phase 6: Production Launch
- 10+ instructors adopt within first month
- 95%+ successful grade uploads
- < 5% error rate
- < 2 hour average support response time

### Long-term Success Metrics
- 50+ grading sessions per semester
- Time savings: 50% reduction in grading time (self-reported)
- Instructor satisfaction: 4/5 or better
- 99.5%+ uptime

---

## Risk Management

### Risk 1: Canvas Admin Approval Delay
**Mitigation:** Start approval process early (Phase 2), maintain good communication with IT

### Risk 2: Performance Issues with Large Files
**Mitigation:** Load testing, optimize image processing, consider cloud storage

### Risk 3: Data Privacy Incident
**Mitigation:** Comprehensive FERPA compliance, audit logging, incident response plan

### Risk 4: Low Adoption
**Mitigation:** Training, good UX, clear value proposition, instructor champions

### Risk 5: Third-Party Service Outages (Anthropic/OpenAI)
**Mitigation:** Graceful degradation, fallback options, cache responses

---

## Support & Maintenance Plan

### Ongoing Responsibilities

**Daily:**
- [ ] Monitor error logs
- [ ] Respond to support tickets

**Weekly:**
- [ ] Review usage metrics
- [ ] Check backup integrity
- [ ] Update dependencies (security patches)

**Monthly:**
- [ ] Review feedback and feature requests
- [ ] Performance optimization
- [ ] Documentation updates

**Quarterly:**
- [ ] Security audit
- [ ] Major version updates
- [ ] Feature releases

**Annually:**
- [ ] Legal/compliance review
- [ ] Infrastructure assessment
- [ ] Long-term roadmap planning

---

## Appendices

### Appendix A: LTI Configuration JSON

```json
{
  "title": "Exam Grading Interface",
  "description": "Web-based tool for grading scanned paper exams with AI assistance",
  "oidc_initiation_url": "https://grading.yourschool.edu/lti/login",
  "target_link_uri": "https://grading.yourschool.edu/lti/launch",
  "public_jwk_url": "https://grading.yourschool.edu/lti/jwks",
  "scopes": [
    "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem",
    "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem.readonly",
    "https://purl.imsglobal.org/spec/lti-ags/scope/result.readonly",
    "https://purl.imsglobal.org/spec/lti-ags/scope/score"
  ],
  "extensions": [
    {
      "platform": "canvas.instructure.com",
      "settings": {
        "placements": [
          {
            "placement": "assignment_selection",
            "message_type": "LtiResourceLinkRequest",
            "target_link_uri": "https://grading.yourschool.edu/lti/launch"
          }
        ]
      }
    }
  ]
}
```

### Appendix B: Environment Variables Reference

Complete list in `.env.example`, key variables:

```bash
# Required
CANVAS_API_KEY_PROD=
CANVAS_API_URL_PROD=https://csumb.instructure.com
ANTHROPIC_API_KEY=

# LTI
LTI_CLIENT_ID=
LTI_DEPLOYMENT_ID=
LTI_ISS=https://canvas.instructure.com

# Application
DATABASE_PATH=/data/grading.db
SECRET_KEY=  # Generate with: openssl rand -hex 32
SESSION_TIMEOUT=3600
LOG_LEVEL=INFO

# Optional
CANVAS_API_KEY_DEV=
CANVAS_API_URL_DEV=https://canvas.beta.instructure.com
OPENAI_API_KEY=
```

### Appendix C: Database Schema Changes Summary

All schema migrations needed across phases:

```sql
-- Phase 3: LTI Context
ALTER TABLE grading_sessions ADD COLUMN lti_context TEXT;
ALTER TABLE grading_sessions ADD COLUMN lti_user_id TEXT;
ALTER TABLE grading_sessions ADD COLUMN lti_roles TEXT;

-- Phase 4: FERPA Compliance
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    canvas_user_id INTEGER NOT NULL,
    canvas_instance TEXT NOT NULL,
    email_hash TEXT,
    role TEXT DEFAULT 'instructor',
    created_at TIMESTAMP,
    last_login TIMESTAMP,
    UNIQUE(canvas_user_id, canvas_instance)
);

ALTER TABLE grading_sessions ADD COLUMN owner_user_id INTEGER;
ALTER TABLE grading_sessions ADD COLUMN shared_with TEXT;
ALTER TABLE grading_sessions ADD COLUMN anonymization_salt TEXT;
ALTER TABLE grading_sessions ADD COLUMN anonymization_enabled INTEGER DEFAULT 1;

ALTER TABLE submissions ADD COLUMN student_id_hash TEXT;
ALTER TABLE submissions ADD COLUMN encrypted_canvas_id TEXT;

CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_id INTEGER,
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id INTEGER,
    details TEXT,
    ip_address TEXT,
    user_agent TEXT
);
CREATE INDEX idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX idx_audit_user ON audit_log(user_id);
```

### Appendix D: Docker Commands Reference

**Build and run:**
```bash
cd docker/web-grading
docker-compose build
docker-compose up -d
```

**View logs:**
```bash
docker-compose logs -f web
```

**Stop and remove:**
```bash
docker-compose down
```

**Backup database:**
```bash
docker-compose exec web cp /data/grading.db /data/grading.db.backup
docker cp $(docker-compose ps -q web):/data/grading.db.backup ./backup.db
```

**Update and restart:**
```bash
git pull
docker-compose build
docker-compose down
docker-compose up -d
```

---

## Conclusion

This roadmap provides a comprehensive plan for deploying the Web Grading Interface from local Docker deployment through production Canvas integration. Each phase builds on the previous, with clear success criteria and deliverables.

**Key Principles:**
- **Iterative:** Build, test, improve, repeat
- **Security-first:** FERPA compliance throughout
- **User-focused:** Documentation and UX matter
- **Sustainable:** Plan for maintenance and support

**Next Steps:**
1. Review and refine this roadmap
2. Begin Phase 1: Docker packaging
3. Track progress using checkboxes
4. Update document as we learn

---

**Document Status:** Living document - update as we progress through phases

**Contributors:**
- Sam Ogden (Lead Developer)
- [Add pilot instructors, IT contacts, etc.]

**Change Log:**
- 2025-10-14: Initial version created
