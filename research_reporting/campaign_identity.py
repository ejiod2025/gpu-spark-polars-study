"""Exact committed campaign/prestart anchors, separate from frozen benchmark code.

Adding a replacement campaign requires a reviewed registry edit with its actual
prestart SHA256. This module has no command-line override or trust-on-first-use
path. Source execution remains restricted to the unchanged 52-file C84 snapshot.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

SOURCE_COMMIT = "c84ef6a07341f350dae6636ab709c1b65fd894e0"
SOURCE_TREE_SHA256 = "9514f0f7ce384763d0a675a9075666707e0edabcc003c729a3e0735a52ea25d8"
ORIGINAL_CAMPAIGN_SHA256 = "69669dfbc74ffb2a0c378c319c7b886043ffccb8f301784fb2b73b9ab32581e0"
ORIGINAL_PRESTART_SHA256 = "f31bca1eeaf8d203fab606ed5e43a1edcdcb23ff5a46de335af1761b3ea36783"
REGISTRY_PATH = Path(__file__).with_name("campaign_identities.json")


def registered_identity(campaign_sha256, prestart_sha256=None):
    """Return only an exact registered identity; unknown runs are never genuine."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate campaign registry key: {key}")
            result[key] = value
        return result

    payload = REGISTRY_PATH.read_bytes()
    registry = json.loads(payload, object_pairs_hook=pairs)
    campaigns = registry.get("campaigns")
    if registry.get("schema_version") != "h100-registered-campaign-identities-v1" or not isinstance(campaigns, dict):
        raise ValueError("Invalid committed campaign identity registry")
    for digest, entry in campaigns.items():
        if (not re.fullmatch(r"[0-9a-f]{64}", digest) or not isinstance(entry, dict)
                or set(entry) != {"label", "prestart_sha256", "source_commit", "source_tree_sha256"}
                or not isinstance(entry["label"], str) or not entry["label"]
                or not isinstance(entry.get("prestart_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry["prestart_sha256"])
                or entry.get("source_commit") != SOURCE_COMMIT
                or entry.get("source_tree_sha256") != SOURCE_TREE_SHA256):
            raise ValueError("Registry entries must bind exact campaign/prestart hashes and unchanged C84 source")
    original = campaigns.get(ORIGINAL_CAMPAIGN_SHA256, {})
    if original.get("prestart_sha256") != ORIGINAL_PRESTART_SHA256:
        raise ValueError("Original campaign/prestart anchor must remain unchanged")
    if campaign_sha256 not in campaigns:
        raise ValueError("Campaign is not in the committed H100 identity registry")
    identity = campaigns[campaign_sha256]
    if prestart_sha256 is not None and prestart_sha256 != identity["prestart_sha256"]:
        raise ValueError("Prestart checksum differs from the registered campaign identity")
    return {**identity, "campaign_sha256": campaign_sha256,
            "registry_path": str(REGISTRY_PATH.resolve()),
            "registry_sha256": hashlib.sha256(payload).hexdigest(),
            "identity_helper_path": str(Path(__file__).resolve()),
            "identity_helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
