#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 VVM platform team — MIT
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: vvm_vm_dr_test
short_description: Non-disruptive DR test of a VM via Materialize SandboxBoot.
version_added: "0.1.0"
description:
  - Calls C(POST /v1/vms/{id}/dr-test) and polls the returned job to terminal.
  - The CP wraps Materialize SandboxBoot to launch a sandboxed copy of the VM
    from the requested recovery point, runs the virtio probe, and auto-tears
    the sandbox down at C(expires_at).
  - Endpoint depends on Materialize. If Materialize SandboxBoot has not yet
    been wired in on the target VVM, the module surfaces a clear
    "Materialize SandboxBoot endpoint pending" message instead of a raw 404.
  - Never touches the Materialize substrate directly.
options:
  vm_id:
    description: VVM VM identifier of the production VM whose recovery point
        is being booted into a sandbox. Must match C([A-Za-z0-9_-]{1,64}).
    type: str
    required: true
  recovery_point_id:
    description: Recovery point to boot into the sandbox. Accepts the literal
        C(latest) or a recovery point identifier. Must match
        C([A-Za-z0-9_-]{1,64}).
    type: str
    required: true
  sandbox_ttl_minutes:
    description: Wall-clock minutes the sandbox lives before auto-teardown.
    type: int
    default: 30
  vvm_api_url:
    description: VVM API base URL. Falls back to VVM_API_URL env.
    type: str
  vvm_api_token:
    description: Bearer token. Falls back to VVM_API_TOKEN env, then
        C(~/.vvm/credentials), then IBMCLOUD_API_KEY → IAM exchange.
        Marked C(no_log) in argument_spec.
    type: str
  timeout_seconds:
    description: Max seconds to wait for the DR-test job to reach a terminal
        state. SandboxBoot can take a while, so default is 3600.
    type: int
    default: 3600
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
- name: Quarterly DR test against the latest recovery point
  ibm.vvm.vvm_vm_dr_test:
    vm_id: vm-7af3
    recovery_point_id: latest

- name: Boot a specific recovery point into a 60-minute sandbox
  ibm.vvm.vvm_vm_dr_test:
    vm_id: vm-7af3
    recovery_point_id: rp-9c2a
    sandbox_ttl_minutes: 60
"""

RETURN = r"""
changed:
  description: True iff a sandbox boot was issued.
  type: bool
  returned: always
vm_id:
  description: Production VM identifier.
  type: str
  returned: always
recovery_point_id:
  description: Echo of the requested recovery point identifier (may be
      C(latest)).
  type: str
  returned: always
sandbox_vm_id:
  description: Identifier of the booted sandbox VM, from
      C(terminal['result']).
  type: str
  returned: when changed and present in job result
expires_at:
  description: Auto-teardown timestamp for the sandbox, from
      C(terminal['result']).
  type: str
  returned: when changed and present in job result
virtio_probe_ok:
  description: Whether the virtio probe inside the sandbox passed.
  type: bool
  returned: when changed and present in job result
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

MODULE_FQCN = "ibm.vvm.vvm_vm_dr_test"
MATERIALIZE_NOT_SHIPPED_MSG = (
    "Materialize SandboxBoot endpoint pending on this VVM "
    "(POST /v1/vms/{id}/dr-test returned 404). "
    "Track delivery via the Materialize Materialize workstream."
)


def run_module():
    argument_spec = dict(
        vm_id=dict(type="str", required=True),
        recovery_point_id=dict(type="str", required=True),
        sandbox_ttl_minutes=dict(type="int", default=30),
        vvm_api_url=dict(type="str"),
        vvm_api_token=dict(type="str", no_log=True),
        timeout_seconds=dict(type="int", default=3600),
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

    vm_id = module.params["vm_id"]
    recovery_point_id = module.params["recovery_point_id"]
    sandbox_ttl_minutes = module.params["sandbox_ttl_minutes"]

    try:
        validate_resource_id(vm_id, "vm_id")
        # 'latest' is also a valid recovery_point_id and passes the pattern.
        validate_resource_id(recovery_point_id, "recovery_point_id")
    except ValueError as e:
        module.fail_json(msg=str(e))
        return
    vm_path = "/v1/vms/" + quote_path_segment(vm_id)

    # Precondition: VM must exist. Recovery point resolution is server-side
    # (the CP knows how to interpret 'latest').
    try:
        client.get(vm_path)
    except ApiError as e:
        fail_json_from_api_error(module, e)
        return

    if module.check_mode:
        module.exit_json(
            changed=True, vm_id=vm_id,
            recovery_point_id=recovery_point_id,
        )
        return

    body = {
        "recovery_point_id": recovery_point_id,
        "sandbox_ttl_minutes": sandbox_ttl_minutes,
    }

    try:
        key = derive_idempotency_key(
            MODULE_FQCN, vm_id, "dr-test",
            key_args={"recovery_point_id": recovery_point_id},
            explicit_override=module.params.get("idempotency_key"),
        )
    except ValueError as e:
        module.fail_json(msg=str(e))
        return

    try:
        resp = client.post(
            vm_path + "/dr-test", body, idempotency_key=key,
        )
        terminal = client.poll_job(
            resp["job_id"],
            timeout_seconds=module.params["timeout_seconds"],
            interval_seconds=module.params["poll_interval_seconds"],
        )
    except ApiError as e:
        if e.status == 404:
            module.fail_json(msg=MATERIALIZE_NOT_SHIPPED_MSG, vm_id=vm_id)
            return
        fail_json_from_api_error(module, e)
        return
    except TimeoutError as e:
        module.fail_json(msg=str(e))
        return

    if terminal.get("state") != "succeeded":
        module.fail_json(
            msg="VVM job ended in %s" % terminal.get("state"),
            job=terminal,
        )
        return

    result = terminal.get("result") or {}
    out = dict(
        changed=True, vm_id=vm_id,
        recovery_point_id=recovery_point_id,
        job=terminal,
    )
    for key_name in ("sandbox_vm_id", "expires_at", "virtio_probe_ok"):
        if key_name in result:
            out[key_name] = result[key_name]
    module.exit_json(**out)


def main():
    run_module()


if __name__ == "__main__":
    main()
