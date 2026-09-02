"""Automated engineering dispatch layer.

The workflows under ``.github/workflows`` are deliberately thin: they gather
GitHub state, hand it to :func:`decide` as JSON, and apply whatever the
decision says. Every routing, validation and idempotency rule lives here so
it can be unit-tested without a GitHub event.

Authority boundary: nothing in this package chooses an agent, chooses a
skill, or decides that work is approved. It only executes an assignment
ChatGPT CTO already recorded in the feature control issue.
"""

from .dispatcher import (
    DISPATCH_MARKER,
    LANE_AUTOMATIC,
    LANE_MANUAL,
    completion,
    control_issue_from_pr,
    decide,
    failure_record,
    parse_record,
    pr_sync,
    release,
    render_record,
)
from .mission import build_mission, extract_section
from .verdict import cto_verdict, parse_verdict, upsert_section
from .routing import (
    EFFORTS,
    MODEL_ALIASES,
    RoutingError,
    allowed_tools,
    claude_args,
    claude_settings,
    parse_worker_routing,
    read_all_worker_routing,
    read_worker_routing,
)
from .status import (
    AGENTS,
    FIELDS,
    SKILLS,
    STATES,
    apply_status,
    parse_status,
    skill_file,
)

__all__ = [
    "AGENTS",
    "DISPATCH_MARKER",
    "EFFORTS",
    "FIELDS",
    "LANE_AUTOMATIC",
    "LANE_MANUAL",
    "MODEL_ALIASES",
    "RoutingError",
    "SKILLS",
    "STATES",
    "apply_status",
    "completion",
    "control_issue_from_pr",
    "failure_record",
    "pr_sync",
    "allowed_tools",
    "build_mission",
    "claude_args",
    "claude_settings",
    "decide",
    "extract_section",
    "parse_record",
    "parse_status",
    "parse_worker_routing",
    "read_all_worker_routing",
    "read_worker_routing",
    "release",
    "render_record",
    "skill_file",
]
