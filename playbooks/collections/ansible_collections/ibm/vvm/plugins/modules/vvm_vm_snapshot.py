#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 VVM platform team — MIT
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: vvm_vm_snapshot
short_description: Manage VM qcow2 snapshots via the VVM API.
version_added: "0.1.0"
description:
  - Manages a named VM snapshot with C(state=present|absent|reverted).
  - C(present) is idempotent when a snapshot of the same name already exists.
  - C(absent) is idempotent when no snapshot of that name is present.
  - C(reverted) requires a matching snapshot; failing-closed if absent.
  - Snapshot endpoints come from the lifecycle API lifecycle API amendment. If the
    amendment has not yet shipped on the target VVM, the module surfaces a
    clear "lifecycle API snapshot endpoints pending" message instead of a raw 404.
  - Returns Ansible's standard C(diff) payload so C(--diff) renders the
    snapshot list before/after the operation.
  - Never touches libvirt or qemu directly.
options:
  vm_id:
    description: VVM VM identifier. Must match C([A-Za-z0-9_-]{1,64}).
    type: str
    required: true
  name:
    description: Snapshot name. Operator-supplied label used for idempotency
        and for C(--diff) rendering.
    type: str
    required: true
  state:
    description: Desired snapshot state. C(present) creates if missing;
        C(absent) deletes if present; C(reverted) reverts the VM to the
        named snapshot (snapshot itself stays in place).
    type: str
    choices: [present, absent, reverted]
    required: true
  description:
    description: Optional human description recorded on C(state=present).
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
    description: Max seconds to wait for the snapshot job to terminal.
    type: int
    default: 1800
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
- name: Take a phase-1 baseline snapshot
  ibm.vvm.vvm_vm_snapshot:
    vm_id: vm-7af3
    name: phase-1-baseline
    state: present

- name: Roll back to the baseline
  ibm.vvm.vvm_vm_snapshot:
    vm_id: vm-7af3
    name: phase-1-baseline
    state: reverted

- name: Reap the baseline once Phase 2 ships
  ibm.vvm.vvm_vm_snapshot:
    vm_id: vm-7af3
    name: phase-1-baseline
    state: absent
"""

RETURN = r"""
changed:
  description: True iff the operation actually changed snapshot state.
  type: bool
  returned: always
vm_id:
  description: VM identifier.
  type: str
  returned: always
name:
  description: Echo of the requested snapshot name.
  type: str
  returned: always
state:
  description: Echo of the requested state.
  type: str
  returned: always
snapshot:
  description: Server-side snapshot record matched by name, when present.
  type: dict
  returned: when state=present and matched, or on terminal success after create
diff:
  description: Ansible diff payload with the snapshot list before and after.
  type: dict
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

MODULE_FQCN = "ibm.vvm.vvm_vm_snapshot"
WS_1_6_AMENDMENT_NOT_SHIPPED_MSG = (
    "lifecycle API snapshot endpoints pending on this VVM "
    "(POST /v1/vms/{id}/snapshots returned 404). "
    "Track delivery via the lifecycle API amendment."
)


def _find_by_name(snapshots, name):
    """Return the first snapshot dict whose 'name' matches, else None."""
    for snap in snapshots:
        if snap.get("name") == name:
            return snap
    return None


def _list_snapshots(client, vm_path):
    """Returns the items list from GET /v1/vms/{id}/snapshots."""
    resp = client.get(vm_path + "/snapshots") or {}
    return resp.get("items") or []


def run_module():
    argument_spec = dict(
        vm_id=dict(type="str", required=True),
        name=dict(type="str", required=True),
        state=dict(
            type="str", required=True,
            choices=["present", "absent", "reverted"],
        ),
        description=dict(type="str"),
        vvm_api_url=dict(type="str"),
        vvm_api_token=dict(type="str", no_log=True),
        timeout_seconds=dict(type="int", default=1800),
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

    vm_id = module.params["vm_id"]
    name = module.params["name"]
    state = module.params["state"]
    description = module.params.get("description")

    try:
        validate_resource_id(vm_id, "vm_id")
    except ValueError as e:
        module.fail_json(msg=str(e))
        return
    vm_path = "/v1/vms/" + quote_path_segment(vm_id)

    try:
        before = _list_snapshots(client, vm_path)
    except ApiError as e:
        if e.status == 404:
            module.fail_json(msg=WS_1_6_AMENDMENT_NOT_SHIPPED_MSG, vm_id=vm_id)
            return
        fail_json_from_api_error(module, e)
        return

    matched = _find_by_name(before, name)

    # Idempotency short-circuits.
    if state == "present" and matched:
        module.exit_json(
            changed=False, vm_id=vm_id, name=name, state=state,
            snapshot=matched, diff={"before": before, "after": before},
        )
        return
    if state == "absent" and not matched:
        module.exit_json(
            changed=False, vm_id=vm_id, name=name, state=state,
            diff={"before": before, "after": before},
        )
        return
    if state == "reverted" and not matched:
        module.fail_json(
            msg="Cannot revert: snapshot %r does not exist on VM %s"
                % (name, vm_id),
            vm_id=vm_id, name=name,
        )
        return

    if module.check_mode:
        module.exit_json(
            changed=True, vm_id=vm_id, name=name, state=state,
            diff={"before": before, "after": before},
        )
        return

    try:
        key = derive_idempotency_key(
            MODULE_FQCN, vm_id, state,
            key_args={"name": name},
            explicit_override=module.params.get("idempotency_key"),
        )
    except ValueError as e:
        module.fail_json(msg=str(e))
        return

    try:
        if state == "present":
            body = {"name": name}
            if description:
                body["description"] = description
            resp = client.post(vm_path + "/snapshots", body,
                               idempotency_key=key)
        elif state == "absent":
            snap_path = vm_path + "/snapshots/" + quote_path_segment(matched["id"])
            resp = client.delete(snap_path, idempotency_key=key)
        else:  # reverted
            snap_path = vm_path + "/snapshots/" + quote_path_segment(matched["id"])
            resp = client.post(snap_path + "/revert", {},
                               idempotency_key=key)
        terminal = client.poll_job(
            resp["job_id"],
            timeout_seconds=module.params["timeout_seconds"],
            interval_seconds=module.params["poll_interval_seconds"],
        )
    except ApiError as e:
        if e.status == 404:
            module.fail_json(msg=WS_1_6_AMENDMENT_NOT_SHIPPED_MSG, vm_id=vm_id)
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

    try:
        after = _list_snapshots(client, vm_path)
    except ApiError as e:
        fail_json_from_api_error(module, e)
        return

    out = dict(
        changed=True, vm_id=vm_id, name=name, state=state,
        diff={"before": before, "after": after},
        job=terminal,
    )
    after_match = _find_by_name(after, name)
    if after_match and state != "absent":
        out["snapshot"] = after_match
    module.exit_json(**out)


def main():
    run_module()


if __name__ == "__main__":
    main()
