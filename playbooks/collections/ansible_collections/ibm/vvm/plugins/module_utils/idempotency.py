# -*- coding: utf-8 -*-
# Copyright (c) 2026 VVM platform team — MIT
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import hashlib
import json
import re

KEY_LENGTH = 32  # truncated sha256 hex; 128 bits is ample for an idempotency key

# explicit_override must be URL-safe and bounded so it cannot smuggle CRLF or
# whitespace into the Idempotency-Key HTTP header on its way to vvm-api.
# Matched with re.fullmatch so a trailing '\n' cannot bypass via Python's
# '$'-matches-before-final-newline behavior.
_VALID_OVERRIDE = re.compile(r"[A-Za-z0-9_-]{16,128}")


def derive_idempotency_key(module_fqcn, target_id, state_or_action, key_args,
                           explicit_override=None):
    """Deterministic 32-hex idempotency key for a VVM action call.

    Key = sha256(canonical_json({
        "fqcn":   module_fqcn,
        "target": target_id,
        "state":  state_or_action,
        "args":   key_args or {},
    }))[:32]

    Canonical JSON (sorted keys, no whitespace) eliminates the delimiter
    injection collision risk of a "|".join() style encoding: no field-value
    combination can simulate a structural delimiter.

    The algorithm is published verbatim in README.md so operators can reproduce
    a key for an explicit_override on a retried run. The two MUST match — drift
    here silently disables idempotency replay protection.

    explicit_override, if non-empty, bypasses derivation. It must match
    [A-Za-z0-9_-]{16,128} so it cannot inject CRLF / whitespace into the
    Idempotency-Key HTTP header; ValueError otherwise.
    """
    if explicit_override:
        if not _VALID_OVERRIDE.fullmatch(explicit_override):
            raise ValueError(
                "explicit_override must match [A-Za-z0-9_-]{16,128}; "
                "got %r" % (explicit_override,)
            )
        return explicit_override
    payload = {
        "fqcn": module_fqcn,
        "target": target_id,
        "state": state_or_action,
        "args": key_args or {},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:KEY_LENGTH]
