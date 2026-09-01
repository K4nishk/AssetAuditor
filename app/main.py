"""FastAPI app factory. Auth, request-id, and metrics middleware land in AA-6 / AA-27."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    return FastAPI(title="AssetAuditor")


app = create_app()
