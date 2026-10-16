#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 VVM platform team — MIT
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: vvm_vm_backup
short_description: Trigger an ad-hoc backup of a VM via the VVM API.
version_added: "0.1.0"
description:
  - Calls C(POST /v1/vms/{id}/backup) and polls the returned job to terminal.
  - The VM must already have a backup policy attached (C(backup_policy_id) set
    on C(GET /v1/vms/{id})); otherwise the module fails closed without issuing
    a POST.
  - Endpoint is provided by image backup (image-level backup engine). If image backup has
    not yet shipped on the target VVM, the module surfaces a clear
    "image backup backup endpoint not yet shipped" message rather than a raw 404.
  - Never touches the backup datamover or qemu directly.
options:
  vm_id:
    description: VVM VM identifier. Must match C([A-Za-z0-9_-]{1,64}).
    type: str
    required: true
  consistency_mode:
    description: Backup consistency requested from the datamover.
        C(application) drives the guest agent; C(crash) does not.
    type: str
    choices: [crash, application]
    default: application
  tag:
    description: Optional operator-supplied tag attached to the resulting
        recovery point. Useful for grouping backups by purpose.
    type: str
  vvm_api_url:
    description: VVM API base URL. Falls back to VVM_API_URL env.
    type: str
  vvm_api_token:
    description: Bearer token. Falls back to VVM_API_TOKEN env, then
        C(~/.vvm/credentials), then IBMCLOUD_API_KEY → IAM exchange.
        Marked C(no_log) in argument_spec.
    type: str
  timeout_seconds:
    description: Max seconds to wait for the backup job to reach a terminal
        state. Backups can take a while, so the default is 3600.
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
- name: Application-consistent backup
  ibm.vvm.vvm_vm_backup:
    vm_id: vm-7af3

- name: Crash-consistent backup tagged for the quarterly DR drill
  ibm.vvm.vvm_vm_backup:
    vm_id: vm-7af3
    consistency_mode: crash
    tag: q3-dr-drill
"""

RETURN = r"""
changed:
  description: True iff a backup job was issued (always true when the policy
      is attached and the endpoint is live).
  type: bool
  returned: always
vm_id:
  description: VM identifier.
  type: str
  returned: always
recovery_point_id:
  description: Identifier of the resulting recovery point, from
      C(terminal['result']).
  type: str
  returned: when changed and present in job result
consistency_mode:
  description: Echo of the requested consistency mode.
  type: str
  returned: always
bytes_written:
  description: Bytes written to the recovery point, from C(terminal['result']).
  type: int
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

MODULE_FQCN = "ibm.vvm.vvm_vm_backup"
WS_2_3_NOT_SHIPPED_MSG = (
    "image backup backup endpoint not yet shipped on this VVM "
    "(POST /v1/vms/{id}/backup returned 404). "
    "Track delivery via the image backup image-backup engine workstream."
)


def run_module():
    argument_spec = dict(
        vm_id=dict(type="str", required=True),
        consistency_mode=dict(
            type="str", choices=["crash", "application"], default="application",
        ),
        tag=dict(type="str"),
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
    consistency_mode = module.params["consistency_mode"]
    tag = module.params.get("tag")

    try:
        validate_resource_id(vm_id, "vm_id")
    except ValueError as e:
        module.fail_json(msg=str(e))
        return
    vm_path = "/v1/vms/" + quote_path_segment(vm_id)

    try:
        vm = client.get(vm_path)
    except ApiError as e:
        fail_json_from_api_error(module, e)
        return

    if not vm.get("backup_policy_id"):
        module.fail_json(
            msg="VM %s has no backup policy attached; attach one before "
                "triggering an ad-hoc backup." % vm_id,
            vm_id=vm_id,
        )
        return

    if module.check_mode:
        module.exit_json(
            changed=True, vm_id=vm_id,
            consistency_mode=consistency_mode,
        )
        return

    body = {"consistency_mode": consistency_mode}
    if tag:
        body["tag"] = tag

    try:
        key = derive_idempotency_key(
            MODULE_FQCN, vm_id, "backup",
            key_args={"consistency_mode": consistency_mode, "tag": tag or ""},
            explicit_override=module.params.get("idempotency_key"),
        )
    except ValueError as e:
        module.fail_json(msg=str(e))
        return

    try:
        resp = client.post(
            vm_path + "/backup", body, idempotency_key=key,
        )
        terminal = client.poll_job(
            resp["job_id"],
            timeout_seconds=module.params["timeout_seconds"],
            interval_seconds=module.params["poll_interval_seconds"],
        )
    except ApiError as e:
        if e.status == 404:
            module.fail_json(msg=WS_2_3_NOT_SHIPPED_MSG, vm_id=vm_id)
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
        consistency_mode=consistency_mode,
        job=terminal,
    )
    if "recovery_point_id" in result:
        out["recovery_point_id"] = result["recovery_point_id"]
    if "bytes_written" in result:
        out["bytes_written"] = result["bytes_written"]
    module.exit_json(**out)


def main():
    run_module()


if __name__ == "__main__":
    main()
