"""The Reflector: a deep agent that reviews one finished session in the background.

It plans with write_todos, reads read-only copies of the session, feedback and chats
from /scratch/, rewrites its own files (brief.md, hypotheses.md, the session summary)
and queues questions for the user. It cannot reach approved values: they are not in
its file system, and value changes become cards the user answers later.

An agent is built for one run, so its tools and files are bound to one user.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    TodoListMiddleware,
)

from guardian import middleware, prompts
from guardian.deps import Deps, ReflectorRun
from guardian.memory import reflector_files
from guardian.tools_reflector import build_reflector_tools

# Model calls per run. A normal review takes about 14. The spec's limit of 25 was
# reached once in a live run (half of a review's calls update its to-do list), so the
# owner raised it to 35. A run that still reaches it fails, is rolled back and retried.
REFLECTOR_MODEL_CALL_LIMIT = 35
MODEL_RETRIES = 2
RETRY_INITIAL_DELAY_SECONDS = 1.0
SKILLS_DIR = Path(__file__).resolve().parent / "skills"

# The only tools the Reflector has. A deep agent also comes with `task` (subagents)
# and `delete`; both are hidden and refused.
ALLOWED_TOOLS = frozenset(
    {
        "write_todos",
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "dump_session",
        "dump_feedback",
        "dump_chatlog",
        "feed_profile",
        "queue_question",
    }
)
# Tools whose results are written entirely by package code. Everything else (file
# reads, searches, the profile) can carry reel text and is labelled as untrusted data.
TRUSTED_TOOLS = frozenset(
    {
        "write_todos",
        "write_file",
        "edit_file",
        "dump_session",
        "dump_feedback",
        "dump_chatlog",
        "queue_question",
    }
)
WRITABLE_PATHS = [
    "/memories/brief.md",
    "/memories/hypotheses.md",
    "/memories/sessions/**",
    "/scratch/**",
]
PRELOADED_FILES = ["/memories/brief.md", "/memories/hypotheses.md"]


def build_backend(deps: Deps, user_id: str) -> CompositeBackend:
    """The Reflector's file system for one user.

    /memories/ holds only that user's own three kinds of file, within their caps;
    /skills/ is the package's skills folder; everything else, including /scratch/,
    lives in the run's state and is gone when the run ends.
    """
    return CompositeBackend(
        default=StateBackend(),
        routes={
            "/memories/": reflector_files.guarded_backend(deps.store, user_id),
            "/skills/": FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True),
        },
    )


def write_permissions() -> list[FilesystemPermission]:
    """A second barrier behind the guarded backend: file tools may write only here."""
    return [
        FilesystemPermission(operations=["write"], paths=WRITABLE_PATHS, mode="allow"),
        FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
    ]


def build_reflector(deps: Deps, model: Any, run: ReflectorRun) -> Any:
    """Build the Reflector for one run."""
    backend = build_backend(deps, run.user_id)
    agent_middleware: list[Any] = [
        TodoListMiddleware(),
        middleware.ToolAllowlist(ALLOWED_TOOLS),
        middleware.frame_text_guard(trusted_tools=TRUSTED_TOOLS),
        ModelRetryMiddleware(
            max_retries=MODEL_RETRIES,
            on_failure="error",
            initial_delay=RETRY_INITIAL_DELAY_SECONDS,
        ),
        ModelCallLimitMiddleware(
            run_limit=REFLECTOR_MODEL_CALL_LIMIT, exit_behavior="error"
        ),
    ]
    return create_deep_agent(
        model=model,
        tools=build_reflector_tools(deps, backend, run),
        system_prompt=prompts.REFLECTOR_PROMPT,
        middleware=agent_middleware,
        skills=["/skills/"],
        memory=PRELOADED_FILES,
        permissions=write_permissions(),
        backend=backend,
        store=deps.store,
    )


def run_input(run: ReflectorRun, values_md: str) -> str:
    """The first message of a run: what to review, and the user's values as read-only text."""
    return (
        f"Review session {run.session_id}.\n"
        f"Collect new feedback and chat since: {run.since.isoformat()}\n\n"
        "The user's approved values (read-only; you cannot change them):\n"
        f"{values_md.strip() or '(none yet)'}\n\n"
        "Follow the session-review skill."
    )
