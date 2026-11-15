"""The Guardian chat agent: small, so it stays fast and within rate limits."""

from __future__ import annotations

from typing import Any, cast

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelRequest,
    ModelRetryMiddleware,
    dynamic_prompt,
)

from guardian import config, key_fallback, middleware, models, prompts
from guardian.deps import ChatRunContext, Deps
from guardian.memory import proposals, reflector_files, values
from guardian.tools_chat import TRUSTED_TOOLS, build_chat_tools

CHAT_MODEL_CALL_LIMIT = 6  # model calls per user message
MODEL_RETRIES = 2
RETRY_INITIAL_DELAY_SECONDS = 1.0


def build_chat_agent(deps: Deps, model: Any) -> Any:
    """Build the chat agent. Approved values and the brief are inserted on every model call."""

    @dynamic_prompt
    def guardian_prompt(request: ModelRequest) -> str:
        user_id = cast(ChatRunContext, request.runtime.context).user_id
        return prompts.render_guardian_prompt(
            values_md=values.render_values(deps.store, user_id),
            brief_md=reflector_files.read_file(
                deps.store, user_id, reflector_files.BRIEF
            ),
        )

    def stored_value_ids(user_id: str) -> list[str]:
        """IDs a reply may name: the stored values, and ones the user has removed
        (the Guardian must be able to say "I removed V2")."""
        stored = [value.id for value in values.list_values(deps.store, user_id)]
        return [*stored, *proposals.removed_value_ids(deps.store, user_id)]

    def with_key(api_key: str) -> Any:
        return models.build_model(config.chat_model_name(), api_key=api_key)

    agent_middleware: list[Any] = [
        key_fallback.middleware(with_key),  # outermost: after the retries below fail
        guardian_prompt,
        middleware.frame_text_guard(trusted_tools=TRUSTED_TOOLS),
        middleware.value_citation_check(stored_value_ids),
        ModelRetryMiddleware(
            max_retries=MODEL_RETRIES,
            on_failure="error",
            initial_delay=RETRY_INITIAL_DELAY_SECONDS,
        ),
        ModelCallLimitMiddleware(
            run_limit=CHAT_MODEL_CALL_LIMIT, exit_behavior="error"
        ),
    ]
    return create_agent(
        model=model,
        tools=build_chat_tools(deps),
        middleware=agent_middleware,
        context_schema=ChatRunContext,
        checkpointer=deps.checkpointer,
    )
