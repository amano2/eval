"""Supabase Backend & Database Client for EvalKit."""

import os
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

_supabase_client: Optional[Client] = None


def get_supabase_client() -> Optional[Client]:
    """Returns an authenticated Supabase client instance if configured."""
    global _supabase_client
    if _supabase_client is None and SUPABASE_URL and SUPABASE_KEY:
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase_client


def is_supabase_enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)
