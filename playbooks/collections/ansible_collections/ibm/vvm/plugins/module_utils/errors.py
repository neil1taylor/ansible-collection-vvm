# -*- coding: utf-8 -*-
# Copyright (c) 2026 VVM platform team — MIT
from __future__ import absolute_import, division, print_function
__metaclass__ = type


def fail_json_from_api_error(module, err):
    """Re-render a vvm_api.ApiError into module.fail_json(...).

    The lifecycle API error envelope ({code, message, details:{fields, blockers}}) is
    flattened into Ansible's idiomatic fail_json shape so playbooks can use
    `register:` + `failed_when:` against typed fields rather than parsing
    nested envelopes by hand.
    """
    payload = {
        "msg": str(err),
        "status_code": err.status,
    }
    if err.code:
        payload["code"] = err.code
    if err.fields:
        payload["validation_errors"] = err.fields
    if err.blockers:
        payload["blockers"] = err.blockers
    if err.envelope:
        payload["envelope"] = err.envelope
    module.fail_json(**payload)
