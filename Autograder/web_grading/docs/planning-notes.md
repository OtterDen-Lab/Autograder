# Grading interface planning

Currently, manual grading is based on generating PDFs and then me annotating them on my ipad.
I've tried to get another flow going with an ios application, but this feels isolated right now, and I think it could be improved by developing a webframework that can host the grading.

## Key Goals
1. provide a grading interface where we present the grader with a single problem response at a time and they can give it a grade
2. summarize results and upload to canvas after completion
3. track statistics about questions

### Stretch goals
1. Have an easier and more consistent way of checking whether names match
2. Able to add in new exams after main grading session has started
3. Adaptability for alternative grading mechanisms
  - How we give grades
    - per-question point assignment, vs buckets of responses getting similar, vs custom rubric
  - How we actually perform grading
    - Can we leverage AI or automation to simplify the process
4. Display per-problem instead of per-page which we currently have, and align exams automatically.
5. Track problem statistics through the exam
  - What's the "ideal" answer and can we use it as an example -- our goal is to get more students to that answer
5. Track problem statistics across years
   - This could include embedding tracking numbers in questions, and seeds in exams so we can figure out what is working and what isn't
   - Database instead of yaml?
   - Reuse questions as in "pick 8x 2 point questions randomly"
6. Ability to enumerate _all_ quesitons
  - This is beginning to get out of scope, interestingly, because that's tying back into the quizgenerator

## General layout ideas

We want to see if we can leverage the same general backend as we have now, where we are leveraging grader classes.
The big difference would be that instead of manual grader pausing, we'd have a WebGrader that would kick up a webUI that users could input things in.

We'd want to disconnect the backend from the frontend if possible, so that if we wanted we could make alternative interfaces for it, such as an ios app.
Therefore, we want to think about how to design this API.

Backend will likely be a database, which for right now can be an SQLite database
  - The question here is how do we store it so we can make sure that it's maintained and semi-secure
  - Can SQL have encrypted easily, or would just stashing it in a secure location be enough?
    - Or maybe just have it exported to the user each time -- that could work, since right now a CSV does that
  - Each entry would be
    - b64 encoded picture of input
    - score
    - comments
    - extras?
  - And we'd basically be querying "give me all the problems I haven't graded yet, in a random order, but only for this particular question"

It would likely make sense to set this up in docker early-ish so we can have a frontend that somebody can just run and then connect.

I think that we should assume this will be running locally right now

## Workflow

The idea of the workflow would be:
1. User selects their canvas course (or enters the course and assignment ID) and sets their credentials
2. User uploads a folder with their exams
3. Exams are preprocessed
   1. Exams are aligned for easier processing 
   2. Names are extracted, matched, and checked and tied to students in canvas
   3. Exams are broken up into questions
3. Grading flow
   1. Each question has each of its submissions graded one-by-one in a random order each time
   2. Each submission is given a score or a note for later grade assignment (e.g. "perfect", "good", "bad", "missing", etc) and feedback (notes or drawings) is collected
4. Once all submissions are graded we display the overall statistics and ask user if they want to upload to canvas
   5. Including a "trial" option that uploads to dev instead of prod
5Uploading is done by recreating the PDFs, adding score and comments to each question, and then uploading
6. At any point users can download their current progress to save for later
   - This could be a CSV or a sqlite database, or if we've getting more advanced a key to connect to their grading session that's stored in a database

---

## Architecture Decisions (From Planning Discussion)

### **Server Model: Persistent Web Service (Option A)**
- FastAPI server runs continuously, handling multiple grading sessions
- Web interface is the primary UI (not CLI-driven start/stop)
- Server lifecycle: Start once → handle uploads/grading/finalization → optionally stays up for next session
- Current exam processing logic extracted into reusable services/libraries

### **Technology Stack**
- **Backend**: FastAPI (async support, SSE, Pydantic validation, auto-generated docs)
- **Frontend**: Vanilla JS initially (no build step, easy future migration to React/Vue)
- **Database**: SQLite with schema versioning (user owns .sqlite file for FERPA compliance)
- **Real-time Updates**: Server-Sent Events (SSE) for status updates during upload/processing
- **Deployment**: Docker Compose for "production", FastAPI dev server during development

### **Key Workflow Changes**
1. **Upload & Preprocessing**: Live progress updates via SSE, automatic name matching where possible
2. **Name Matching UI**: Interactive interface when auto-match fails (vs manual CSV editing)
3. **Grading Flow**: Problem-first approach (grade all Q1, then Q2, etc.) with anonymous display by default
4. **Real-time Persistence**: All state saved immediately to SQLite (crash recovery built-in)

### **Database Design Notes**
- Store problem images as base64 in database (portability over performance)
- Schema versioning for future migrations
- Support for display_name vs student_name (future anonymization with hashed names)
- Problem numbering per-exam initially, extensible to cross-exam tracking later

### **Future Work (Tracked in todo.md)**
- Drawing annotations (high priority - replicating iPad PDF annotation workflow)
- FERPA-compliant anonymization with salted hashes
- Cross-exam question tracking (problem_type, version, seed)
- Student performance filtering and collaboration detection
- WebSocket upgrade if bidirectional communication becomes necessary
