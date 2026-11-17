"""Pytest configuration and global fixtures for EvalKit."""

import pytest
from backend.app.db.session import init_db


@pytest.fixture(scope="session", autouse=True)
def initialize_database():
    """Ensures all database tables exist before running any tests."""
    init_db()
