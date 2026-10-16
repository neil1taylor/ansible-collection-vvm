#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 VVM platform team — MIT
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: vvm_vm_power
short_description: Drive a VM's power state via the VVM API.
version_added: "0.1.0"
description:
  - Calls C(POST /v1/vms/{id}/power) and polls the returned job to terminal.
  - Idempotent on C(started)/C(stopped); always-fire on C(rebooted)/C(reset)/C(shutdown_guest).
  - Never touches libvirt, qemu, or IBM Cloud SDK directly.
options:
  vm_id:
    description: VVM VM identifier.
    type: str
    required: true
  state:
    description: Target power state. C(started)/C(stopped) are idempotent; the
        rest always fire.
    type: str
    required: true
    choices: [started, stopped, rebooted, reset, shutdown_guest]
  vvm_api_url:
    description: VVM API base URL. Falls back to VVM_API_URL env.
    type: str
  vvm_api_token:
    description: Bearer token. Falls back to VVM_API_TOKEN env, then
        C(~/.vvm/credentials), then IBMCLOUD_API_KEY → IAM exchange.
        Marked C(no_log) in argument_spec.
    type: str
  timeout_seconds:
    description: Max seconds to wait for the job to reach a terminal state.
    type: int
    default: 1800
  poll_interval_seconds:
    description: Seconds between job polls.
    type: int
    default: 2
  idempotency_key:
    description: Optional explicit Idempotency-Key. Must match
        C([A-Za-z0-9_-]{16,128}); ValueError otherwise. If omitted, derived
        per the algorithm published in the collection README.
    type: str
  validate_certs:
    description: Verify TLS certificates on the VVM API.
    type: bool
    default: true
author:
  - VVM platform team (@vvm)
"""

EXAMPLES = r"""
- name: Boot a VM
  ibm.vvm.vvm_vm_power:
    vm_id: vm-7af3
    state: started

- name: Reboot a VM (always fires)
  ibm.vvm.vvm_vm_power:
    vm_id: vm-7af3
    state: rebooted
"""

RETURN = r"""
changed:
  description: True iff the power action was issued (false on idempotent no-op).
  type: bool
  returned: always
vm_id:
  description: VM identifier.
  type: str
  returned: always
state_before:
  description: VM's C(actual_state) read before the action.
  type: str
  returned: always
state_after:
  description: VM's C(actual_state) read after the action completes; equal to
      state_before on idempotent no-op.
  type: str
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

MODULE_FQCN = "ibm.vvm.vvm_vm_power"

# state → (api action, expected actual_state when idempotent, else None for
# "always fire — no idempotent target")
STATE_TO_ACTION = {
    "started": ("start", "Running"),
    "stopped": ("stop", "Stopped"),
    "rebooted": ("reboot", None),
    "reset": ("reset", None),
    "shutdown_guest": ("shutdown_guest", None),
}


def run_module():
    argument_spec = dict(
        vm_id=dict(type="str", required=True),
        state=dict(type="str", required=True, choices=list(STATE_TO_ACTION)),
        vvm_api_url=dict(type="str"),
        vvm_api_token=dict(type="str", no_log=True),
        timeout_seconds=dict(type="int", default=1800),
        poll_interval_seconds=dict(type="int", default=2),
        # idempotency_key is a 32-hex sha256 prefix or an operator-supplied
        # URL-safe token; explicitly not a secret.
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
    state = module.params["state"]
    action, target_actual = STATE_TO_ACTION[state]

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

    state_before = vm.get("actual_state")
    if target_actual and state_before == target_actual:
        module.exit_json(
            changed=False, vm_id=vm_id,
            state_before=state_before, state_after=state_before,
        )
        return

    if module.check_mode:
        module.exit_json(
            changed=True, vm_id=vm_id,
            state_before=state_before,
            state_after=target_actual or "transient",
        )
        return

    try:
        key = derive_idempotency_key(
            MODULE_FQCN, vm_id, state, key_args={},
            explicit_override=module.params.get("idempotency_key"),
        )
    except ValueError as e:
        module.fail_json(msg=str(e))
        return

    try:
        resp = client.post(
            vm_path + "/power",
            {"action": action}, idempotency_key=key,
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
            msg="VVM job ended in %s" % terminal.get("state"),
            job=terminal,
        )
        return

    try:
        vm_after = client.get(vm_path)
    except ApiError as e:
        fail_json_from_api_error(module, e)
        return

    module.exit_json(
        changed=True, vm_id=vm_id,
        state_before=state_before,
        state_after=vm_after.get("actual_state"),
        job=terminal,
    )


def main():
    run_module()


if __name__ == "__main__":
    main()
