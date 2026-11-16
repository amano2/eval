"""Payload storage manager for offloading large payloads (>64KB) to Supabase Storage."""

import os
import json
import hashlib
from typing import Any, Tuple, Optional
from datetime import datetime, timezone

from backend.app.db.supabase_client import get_supabase_client, is_supabase_enabled

MAX_INLINE_PAYLOAD_BYTES = 65536  # 64 KB threshold


def process_payload_for_storage(payload: Any, path_prefix: str = "payloads") -> Tuple[Any, Optional[str]]:
    """Inspects payload size. If >64KB, offloads to Supabase Storage and returns (preview, storage_key)."""
    if payload is None:
        return None, None

    try:
        serialized = json.dumps(payload)
    except Exception:
        serialized = str(payload)

    raw_bytes = serialized.encode("utf-8")
    if len(raw_bytes) <= MAX_INLINE_PAYLOAD_BYTES:
        return payload, None

    # Offload large payload
    payload_hash = hashlib.sha256(raw_bytes).hexdigest()[:16]
    today_str = datetime.now(timezone.utc).strftime("%Y_%m_%d")
    storage_key = f"{path_prefix}/{today_str}/{payload_hash}.json"

    # Attempt Supabase Storage upload
    uploaded = False
    if is_supabase_enabled():
        try:
            sb = get_supabase_client()
            if sb:
                sb.storage.from_("evalkit-payloads").upload(
                    file=raw_bytes,
                    path=storage_key,
                    file_options={"content-type": "application/json", "upsert": "true"}
                )
                uploaded = True
        except Exception:
            uploaded = False

    # Local disk fallback if storage upload fails or offline
    if not uploaded:
        local_dir = os.path.join(os.getenv("OBJECT_STORAGE_DIR", "./storage"), path_prefix, today_str)
        os.makedirs(local_dir, exist_ok=True)
        local_path = os.path.join(local_dir, f"{payload_hash}.json")
        with open(local_path, "wb") as f:
            f.write(raw_bytes)

    preview_text = serialized[:250] + "... [payload offloaded to storage]"
    preview = {
        "_preview": preview_text,
        "_storage_key": storage_key,
        "_size_bytes": len(raw_bytes),
        "_truncated": True
    }
    return preview, storage_key
