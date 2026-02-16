# Architecture Overview

This diagram shows the core control/data flow for a grading run and how major components interact.

```mermaid
flowchart TD
  Y[YAML Config]
  CLI[CLI\n`grade-assignments`]
  CM[config_models\nparse + normalize]
  REG[Registry\nAssignmentRegistry + GraderRegistry]
  ORCH[Orchestration\nprepare/grade/publish]
  ASSIGN[Assignment\nProgrammingAssignment / TextAssignment]
  GRADER[Grader\nTemplateGrader / TextSubmissionGrader]
  REPORT[Reporting\nJSON + console + Slack]

  subgraph External Systems
    CANVAS[(Canvas LMS API)]
    DOCKER[(Docker Engine)]
    LLM[(OpenAI / Anthropic)]
    SLACK[(Slack API/Webhook)]
  end

  Y --> CLI --> CM --> REG --> ORCH
  ORCH --> ASSIGN
  ASSIGN --> CANVAS
  ORCH --> GRADER
  GRADER --> DOCKER
  GRADER --> LLM
  ORCH --> REPORT
  REPORT --> SLACK
  ORCH --> CANVAS
```

## Notes

- `config_models` validates top-level schema and grader settings before work begins.
- Registry lookup enforces grader/kind compatibility and dispatches grader-specific settings normalization.
- Programming grading depends on Docker; text grading can call LLM providers.
- Publish/finalize writes grades/feedback back to Canvas and emits run-level reporting.
