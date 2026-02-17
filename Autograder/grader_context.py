from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GraderContext:
  """Typed runtime context passed to graders by the orchestration layer."""

  course_name: Optional[str] = None
  assignment_name: Optional[str] = None
  assignment_kind: Optional[str] = None
  slack_channel: Optional[str] = None
  privacy_mode: str = "id_only"
  reveal_identity: bool = False
  records_dir: Optional[str] = None
  prefer_anthropic: bool = False
  idempotency_key: Optional[str] = None
  idempotency_state_dir: Optional[str] = None
