"""HTTP routes for the Guardian, for the team's server to include.

    from guardian_router import router as guardian_router
    app.include_router(guardian_router)

The router reads its data source from `app.state.guardian_datasource` (the Judge
server sets this to its `JudgeDataSource`) and configures the Guardian on the first
request. `review_sessions(user_id)` is the function to run as a background task when
a session ends. `create_app()` is a standalone server on seed data.
"""

from guardian_router.background import review_sessions
from guardian_router.routes import router
from guardian_router.standalone import create_app

__all__ = ["create_app", "review_sessions", "router"]
