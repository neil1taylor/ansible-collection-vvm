#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 VVM platform team — MIT
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: vvm_host_drain
short_description: Drain VMs off a VVM host (maintenance verb).
version_added: "0.1.0"
description:
  - Calls C(POST /v1/hosts/{id}/drain) to start the host drain.
  - With C(poll_until_empty=true) (the default) the module polls the
    returned job to terminal and surfaces C(vms_migrated) /
    C(vms_failed) from C(terminal['result']).
  - With C(poll_until_empty=false) the module fires the drain and returns
    immediately with the C(job_id) — use the playbook's own polling loop
    or C(ibm.vvm.vvm_host_drain_cancel) to manage the in-flight drain.
  - Idempotent C(check_mode) on already-draining hosts (C(desired_state ==
    Draining) → C(changed=false)).
  - Never reaches around the VVM API to libvirt or
    the host agent.
options:
  host_id:
    description: VVM host identifier. Must match C([A-Za-z0-9_-]{1,64}).
    type: str
    required: true
  reason:
    description: Operator-supplied free-text reason for the drain. Recorded
        against the resulting drain job for audit.
    type: str
    default: ""
  poll_until_empty:
    description: If true (default), poll the drain job until terminal and
        surface per-VM outcomes. If false, fire and return the job_id
        immediately.
    type: bool
    default: true
  vvm_api_url:
    description: VVM API base URL. Falls back to VVM_API_URL env.
    type: str
  vvm_api_token:
    description: Bearer token. Falls back to VVM_API_TOKEN env, then
        C(~/.vvm/credentials), then IBMCLOUD_API_KEY → IAM exchange.
        Marked C(no_log) in argument_spec.
    type: str
  timeout_seconds:
    description: Max seconds to wait for the drain job to reach a terminal
        state. Drain runtimes scale with VM count, so default is 7200.
    type: int
    default: 7200
  poll_interval_seconds:
    description: Seconds between job polls.
    type: int
    default: 5
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
- name: Drain a host for maintenance and follow until empty
  ibm.vvm.vvm_host_drain:
    host_id: host-9
    reason: "BIOS firmware update"

- name: Kick off a drain without waiting
  ibm.vvm.vvm_host_drain:
    host_id: host-9
    reason: "scheduled maintenance"
    poll_until_empty: false
"""

RETURN = r"""
changed:
  description: True iff a drain was issued (false on already-draining check_mode).
  type: bool
  returned: always
host_id:
  description: Host identifier.
  type: str
  returned: always
job_id:
  description: The drain job's identifier. Always returned when a POST was
      issued, even when C(poll_until_empty=false).
  type: str
  returned: when changed
vms_migrated:
  description: Per-VM successful migration entries from C(terminal['result']).
  type: list
  returned: when changed and poll_until_empty and present in job result
vms_failed:
  description: Per-VM failed migration entries from C(terminal['result']).
  type: list
  returned: when changed and poll_until_empty and present in job result
job:
  description: Terminal job envelope from C(GET /v1/jobs/{id}).
  type: dict
  returned: when changed and poll_until_empty
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

MODULE_FQCN = "ibm.vvm.vvm_host_drain"


def run_module():
    argument_spec = dict(
        host_id=dict(type="str", required=True),
        reason=dict(type="str", default=""),
        poll_until_empty=dict(type="bool", default=True),
        vvm_api_url=dict(type="str"),
        vvm_api_token=dict(type="str", no_log=True),
        timeout_seconds=dict(type="int", default=7200),
        poll_interval_seconds=dict(type="int", default=5),
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
    reason = module.params["reason"]
    poll_until_empty = module.params["poll_until_empty"]

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

    # If the host is already draining and we're in check_mode, that's
    # a no-op projection (operator can re-issue safely when the in-flight
    # drain finishes).
    if module.check_mode and host.get("desired_state") == "Draining":
        module.exit_json(changed=False, host_id=host_id)
        return

    if module.check_mode:
        module.exit_json(changed=True, host_id=host_id)
        return

    try:
        key = derive_idempotency_key(
            MODULE_FQCN, host_id, "drain", key_args={},
            explicit_override=module.params.get("idempotency_key"),
        )
    except ValueError as e:
        module.fail_json(msg=str(e))
        return

    try:
        resp = client.post(
            host_path + "/drain", {"reason": reason}, idempotency_key=key,
        )
    except ApiError as e:
        fail_json_from_api_error(module, e)
        return

    job_id = resp["job_id"]

    if not poll_until_empty:
        module.exit_json(changed=True, host_id=host_id, job_id=job_id)
        return

    try:
        terminal = client.poll_job(
            job_id,
            timeout_seconds=module.params["timeout_seconds"],
            interval_seconds=module.params["poll_interval_seconds"],
        )
    except TimeoutError as e:
        module.fail_json(msg=str(e), host_id=host_id, job_id=job_id)
        return

    if terminal.get("state") != "succeeded":
        module.fail_json(
            msg="VVM drain job ended in %s" % terminal.get("state"),
            host_id=host_id, job_id=job_id, job=terminal,
        )
        return

    result = terminal.get("result") or {}
    out = dict(
        changed=True, host_id=host_id, job_id=job_id, job=terminal,
    )
    if "vms_migrated" in result:
        out["vms_migrated"] = result["vms_migrated"]
    if "vms_failed" in result:
        out["vms_failed"] = result["vms_failed"]
    module.exit_json(**out)


def main():
    run_module()


if __name__ == "__main__":
    main()
