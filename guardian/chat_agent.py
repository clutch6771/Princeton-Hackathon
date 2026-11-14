"""The Guardian chat agent: small, so it stays fast and within rate limits."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRequest, ModelRetryMiddleware, dynamic_prompt

from guardian import prompts
from guardian.deps import ChatRunContext, Deps
from guardian.memory import reflector_files, values
from guardian.tools_chat import build_chat_tools

CHAT_MODEL_CALL_LIMIT = 6  # model calls per user message
MODEL_RETRIES = 2
RETRY_INITIAL_DELAY_SECONDS = 1.0


def build_chat_agent(deps: Deps, model: Any) -> Any:
    """Build the chat agent. Approved values and the brief are inserted on every model call."""

    @dynamic_prompt
    def guardian_prompt(request: ModelRequest) -> str:
        user_id = request.runtime.context.user_id
        return prompts.render_guardian_prompt(
            values_md=values.render_values(deps.store, user_id),
            brief_md=reflector_files.read_file(deps.store, user_id, reflector_files.BRIEF),
        )

    middleware = [
        guardian_prompt,
        ModelRetryMiddleware(
            max_retries=MODEL_RETRIES, on_failure="error", initial_delay=RETRY_INITIAL_DELAY_SECONDS
        ),
        ModelCallLimitMiddleware(run_limit=CHAT_MODEL_CALL_LIMIT, exit_behavior="error"),
    ]
    return create_agent(
        model=model,
        tools=build_chat_tools(deps),
        middleware=middleware,
        context_schema=ChatRunContext,
        checkpointer=deps.checkpointer,
    )
