"""
AgentDock API entrypoint.

  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

from app import app

__all__ = ["app"]
