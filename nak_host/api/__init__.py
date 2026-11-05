"""
NAK API
==========================================================

This module handles the FastAPI application setup and configuration.
Mirrors NebulonDB's `ndb_host/api/__init__.py`.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        FastAPI: Configured FastAPI instance
    """
    app = FastAPI(
        title="NebulonAK Laya & SystemOne API",
        description="Secure API to serve laya / systemone decisions",
        version="0.1.0",
        openapi_tags=[
            {
                "name": "Laya",
                "description": "Laya decision endpoints",
            },
            {
                "name": "SystemOne",
                "description": "SystemOne decision endpoints",
            },
        ],
    )

    # === Middleware Setup ===
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    return app
