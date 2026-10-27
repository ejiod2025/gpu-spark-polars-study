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
ABORTED_V1_CAMPAIGN_SHA256 = "0dabe0d40c5adda0a087508e3f95ceb0636815f5133a3b401071612e3f24c145"
ABORTED_V1_PRESTART_SHA256 = "a758ceea7d16a6c726221db5db2cb99502ac4f640131341b456e9b63ed459b2a"
RECOVERY_CAMPAIGN_ID = "h100-confirmatory-gclocker100-20261027"
LEGACY_FIELDS = {"label", "prestart_sha256", "source_commit", "source_tree_sha256"}
RECOVERY_FIELDS = LEGACY_FIELDS | {"campaign_id", "execution_profile_sha256"}
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
    recovery_entries = []
    for digest, entry in campaigns.items():
        if (not re.fullmatch(r"[0-9a-f]{64}", digest) or not isinstance(entry, dict)
                or set(entry) not in (LEGACY_FIELDS, RECOVERY_FIELDS)
                or not isinstance(entry["label"], str) or not entry["label"]
                or not isinstance(entry.get("prestart_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry["prestart_sha256"])
                or entry.get("source_commit") != SOURCE_COMMIT
                or entry.get("source_tree_sha256") != SOURCE_TREE_SHA256):
            raise ValueError("Registry entries must bind exact campaign/prestart hashes and unchanged C84 source")
        if set(entry) == RECOVERY_FIELDS:
            if (entry["campaign_id"] != RECOVERY_CAMPAIGN_ID
                    or not isinstance(entry["execution_profile_sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", entry["execution_profile_sha256"])
                    or digest in {ORIGINAL_CAMPAIGN_SHA256, ABORTED_V1_CAMPAIGN_SHA256}):
                raise ValueError("Only the fixed recovery campaign may register a JVM execution profile")
            recovery_entries.append(digest)
    if len(recovery_entries) > 1:
        raise ValueError("Only one recovery campaign identity may be registered")
    original = campaigns.get(ORIGINAL_CAMPAIGN_SHA256, {})
    if original.get("prestart_sha256") != ORIGINAL_PRESTART_SHA256:
        raise ValueError("Original campaign/prestart anchor must remain unchanged")
    aborted = campaigns.get(ABORTED_V1_CAMPAIGN_SHA256)
    if aborted is not None and aborted.get("prestart_sha256") != ABORTED_V1_PRESTART_SHA256:
        raise ValueError("Aborted v1 campaign/prestart anchor must remain unchanged")
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
