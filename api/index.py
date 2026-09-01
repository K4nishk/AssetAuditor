"""Vercel Python entry point — mounts the FastAPI app for serverless invocation."""

from app.main import app

__all__ = ["app"]
