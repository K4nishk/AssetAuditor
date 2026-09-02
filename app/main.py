"""FastAPI app factory. Auth, request-id, and metrics middleware land in AA-6 / AA-27.

Routes are mounted under `/api` so `vercel.json`'s `/api/:path*` rewrite (the
only rule that reaches this function) matches the exact path FastAPI sees —
Vercel forwards the original request path unchanged, it does not strip the
rewrite destination. Every route module added by a later issue should follow
the same `/api/...` convention.
"""

from fastapi import FastAPI

from app.routes.manual_entry import router as manual_entry_router
from app.routes.staged import router as staged_router
from app.routes.uploads import router as uploads_router


def create_app() -> FastAPI:
    app = FastAPI(title="AssetAuditor")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "AssetAuditor"}

    app.include_router(uploads_router)
    app.include_router(staged_router)
    app.include_router(manual_entry_router)

    return app


app = create_app()
