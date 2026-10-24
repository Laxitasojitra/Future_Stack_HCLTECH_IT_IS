"""Lets tests for a teammate's agent skip until that agent is merged into this branch."""

import importlib

import pytest


def requires_agent(module):
    """Skip while app/agents/<module>.py is still a placeholder; the test runs again once the real file is merged."""
    is_placeholder = "PLACEHOLDER" in (importlib.import_module(f"app.agents.{module}").__doc__ or "")
    return pytest.mark.skipif(is_placeholder, reason=f"app/agents/{module}.py is a placeholder on this branch")
