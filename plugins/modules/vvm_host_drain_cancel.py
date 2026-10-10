#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 VVM platform team — MIT
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: vvm_host_drain_cancel
short_description: Cancel an in-flight drain of a VVM host.
version_added: "0.1.0"
description:
  - Calls C(POST /v1/hosts/{id}/drain/cancel) to abort the host's in-flight
    drain. VMs already migrated stay where they are; remaining VMs stop
    being scheduled off the host.
  - Idempotent — when the host is not draining (C(desired_state != Draining))
    returns C(changed=false) without issuing a POST.
options:
  host_id:
    description: VVM host identifier. Must match C([A-Za-z0-9_-]{1,64}).
    type: str
    required: true
  vvm_api_url:
    description: VVM API base URL. Falls back to VVM_API_URL env.
    type: str
  vvm_api_token:
    description: Bearer token. Falls back to VVM_API_TOKEN env, then
        C(~/.vvm/credentials), then IBMCLOUD_API_KEY → IAM exchange.
        Marked C(no_log) in argument_spec.
    type: str
  timeout_seconds:
    description: Max seconds to wait for the cancel job to reach a terminal
        state. Cancels are short, so default is 300.
    type: int
    default: 300
  poll_interval_seconds:
    description: Seconds between job polls.
    type: int
    default: 2
  idempotency_key:
    description: Optional explicit Idempotency-Key. Must match
        C([A-Za-z0-9_-]{16,128}); ValueError otherwise.
    type: str
  validate_certs:
    description: Verify TLS certificates on the VVM API.
    type: bool
    default: true
author:
  - VVM platform team (@vvm)
"""

EXAMPLES = r"""
- name: Cancel the in-flight drain of host-9
  ibm.vvm.vvm_host_drain_cancel:
    host_id: host-9
"""

RETURN = r"""
changed:
  description: True iff a cancel was issued (false on not-draining no-op).
  type: bool
  returned: always
host_id:
  description: Host identifier.
  type: str
  returned: always
cancelled_at:
  description: Timestamp when the cancel landed, from C(terminal['result']).
  type: str
  returned: when changed and present in job result
vms_still_on_host:
  description: VMs that remained on the host at cancel time.
  type: list
  returned: always
job:
  description: Terminal job envelope from C(GET /v1/jobs/{id}).
  type: dict
  returned: when changed
"""

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.ibm.vvm.plugins.module_utils.vvm_api import (
    ApiClient, ApiError, quote_path_segment, resolve_api_url, resolve_token,
    validate_resource_id,
)
from ansible_collections.ibm.vvm.plugins.module_utils.errors import (
    fail_json_from_api_error,
)
from ansible_collections.ibm.vvm.plugins.module_utils.idempotency import (
    derive_idempotency_key,
)

MODULE_FQCN = "ibm.vvm.vvm_host_drain_cancel"


def run_module():
    argument_spec = dict(
        host_id=dict(type="str", required=True),
        vvm_api_url=dict(type="str"),
        vvm_api_token=dict(type="str", no_log=True),
        timeout_seconds=dict(type="int", default=300),
        poll_interval_seconds=dict(type="int", default=2),
        idempotency_key=dict(type="str", no_log=False),
        validate_certs=dict(type="bool", default=True),
    )
    module = AnsibleModule(argument_spec=argument_spec, supports_check_mode=True)

    try:
        api_url = resolve_api_url(module.params.get("vvm_api_url"))
        token = resolve_token(module.params.get("vvm_api_token"))
    except RuntimeError as e:
        module.fail_json(msg=str(e))
        return

    client = ApiClient(
        api_url, token,
        validate_certs=module.params["validate_certs"],
    )

    host_id = module.params["host_id"]

    try:
        validate_resource_id(host_id, "host_id")
    except ValueError as e:
        module.fail_json(msg=str(e))
        return
    host_path = "/v1/hosts/" + quote_path_segment(host_id)

    try:
        host = client.get(host_path)
    except ApiError as e:
        fail_json_from_api_error(module, e)
        return

    vms_still_on_host = host.get("current_vm_ids", []) or []

    if host.get("desired_state") != "Draining":
        module.exit_json(
            changed=False, host_id=host_id,
            vms_still_on_host=vms_still_on_host,
        )
        return

    if module.check_mode:
        module.exit_json(
            changed=True, host_id=host_id,
            vms_still_on_host=vms_still_on_host,
        )
        return

    try:
        key = derive_idempotency_key(
            MODULE_FQCN, host_id, "drain-cancel", key_args={},
            explicit_override=module.params.get("idempotency_key"),
        )
    except ValueError as e:
        module.fail_json(msg=str(e))
        return

    try:
        resp = client.post(
            host_path + "/drain/cancel", {}, idempotency_key=key,
        )
        terminal = client.poll_job(
            resp["job_id"],
            timeout_seconds=module.params["timeout_seconds"],
            interval_seconds=module.params["poll_interval_seconds"],
        )
    except ApiError as e:
        fail_json_from_api_error(module, e)
        return
    except TimeoutError as e:
        module.fail_json(msg=str(e))
        return

    if terminal.get("state") != "succeeded":
        module.fail_json(
            msg="VVM drain-cancel job ended in %s" % terminal.get("state"),
            job=terminal,
        )
        return

    result = terminal.get("result") or {}
    out = dict(
        changed=True, host_id=host_id, job=terminal,
        vms_still_on_host=result.get("vms_still_on_host", vms_still_on_host),
    )
    if "cancelled_at" in result:
        out["cancelled_at"] = result["cancelled_at"]
    module.exit_json(**out)


def main():
    run_module()


if __name__ == "__main__":
    main()
