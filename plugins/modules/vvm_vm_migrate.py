#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 VVM platform team — MIT
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: vvm_vm_migrate
short_description: Live-migrate a VM to a named host via the VVM API.
version_added: "0.1.0"
description:
  - Calls C(POST /v1/vms/{id}/migrate) with C(dest_host_id), then polls
    C(GET /v1/migrations/{id}) until the migration is terminal and fails unless
    it completed.
  - B(A destination is required.) The control plane has no scheduler-chosen
    placement for a move: C(dest_host_id) is the only field the route decodes.
    C(auto_place) is accepted as an argument so that a playbook written against
    the older module fails with that sentence rather than with a 400 from the
    server, and it never migrates anything.
  - Idempotent when the VM is already on C(dest_host) — returns
    C(changed=false) without issuing a POST.
  - B(C(check_mode) asks the control plane for nothing.) There is no dry-run
    route and no admission preflight; the module reports the change it would
    make and performs none. It used to post to C(?dry_run=true), a query string
    the route ignores, so a check run would have performed a real migration had
    the body been valid.
  - Never touches libvirt or the IBM Cloud SDK directly.
options:
  vm_id:
    description: VVM VM identifier.
    type: str
    required: true
  dest_host:
    description: Target host identifier. Required unless the VM is already there.
    type: str
  auto_place:
    description: B(Unsupported.) Present only so that it is refused with a
        message naming C(dest_host). The API requires an explicit destination.
    type: bool
    default: false
  max_bandwidth_mib_s:
    description: Speed limit on the memory copy, in MiB/s. Omit for no limit.
    type: int
  timeout_seconds:
    description: Max seconds to wait for the migration to become terminal.
    type: int
    default: 1800
  poll_interval_seconds:
    description: Seconds between migration polls.
    type: int
    default: 2
  idempotency_key:
    description: Optional explicit Idempotency-Key. Must match
        C([A-Za-z0-9_-]{16,128}); ValueError otherwise.
    type: str
  vvm_api_url:
    description: VVM API base URL. Falls back to VVM_API_URL env.
    type: str
  vvm_api_token:
    description: Bearer token. Falls back to VVM_API_TOKEN env, then
        C(~/.vvm/credentials), then IBMCLOUD_API_KEY → IAM exchange.
        Marked C(no_log) in argument_spec.
    type: str
  validate_certs:
    description: Verify TLS certificates on the VVM API.
    type: bool
    default: true
author:
  - VVM platform team (@vvm)
"""

EXAMPLES = r"""
- name: Migrate to a specific host
  ibm.vvm.vvm_vm_migrate:
    vm_id: "{{ vm_id }}"
    dest_host: "{{ other_host_id }}"

- name: Migrate, holding the copy to 64 MiB/s
  ibm.vvm.vvm_vm_migrate:
    vm_id: "{{ vm_id }}"
    dest_host: "{{ other_host_id }}"
    max_bandwidth_mib_s: 64
"""

RETURN = r"""
changed:
  description: True iff a migration was issued (false on already-on-target).
  type: bool
  returned: always
vm_id:
  description: VM identifier.
  type: str
  returned: always
source_host:
  description: VM's C(current_host_id) read before the migration.
  type: str
  returned: always
destination_host:
  description: Target host — the supplied C(dest_host), or the VM's
      C(current_host_id) after the move.
  type: str
  returned: always
migration_id:
  description: The C(migration_id) C(POST /v1/vms/{id}/migrate) returned.
  type: str
  returned: when changed
migration:
  description: The terminal migration as C(GET /v1/migrations/{id}) publishes it.
  type: dict
  returned: when changed
downtime_ms:
  description:
    - Milliseconds the guest was paused for the cutover, as the control plane
      publishes it on the migration.
    - Absent when the host reported no measurement, and also absent when it
      reported C(0). A zero cannot currently be told from a cutover nobody
      measured - libvirt-control drops libvirt's measured flag and answers 0 for
      a completion event it never saw - so it is not returned as a measurement.
      The control plane refuses it in the same two places, its migration
      endpoint and its downtime metric, and the raw value is still recorded
      against the migration row.
    - A genuine sub-millisecond cutover is therefore invisible here until that
      flag is carried through. When it is, a returned C(0) becomes a real
      measurement again.
  type: int
  returned: when changed and the host reported a non-zero measurement
precopy_iterations:
  description:
    - Pre-copy iterations libvirt ran through before the cutover.
    - Named for the wire field, and read off the migration rather than out of a
      job result. The job result was never reachable at all, because the migrate
      route answers a C(migration_id) and no job id.
  type: int
  returned: when changed and the host reported a measurement
"""

import time

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

MODULE_FQCN = "ibm.vvm.vvm_vm_migrate"

_NO_AUTO_PLACE = (
    "auto_place is not supported: POST /v1/vms/{id}/migrate decodes only "
    "dest_host_id and the control plane has no scheduler-chosen placement for "
    "a move. Name the target host in dest_host."
)

# ⛔ postVMMigrateResp CARRIES migration_id AND state. NOTHING ANYWHERE SENDS
# job_id ON THIS ROUTE. Reading one was an unhandled KeyError on every call that
# got past the body, so the field is named in the message instead.
_NO_MIGRATION_ID = (
    "POST /v1/vms/%s/migrate answered without a migration_id, so there is no "
    "migration to follow. Response: %r"
)


def _wait_for_migration(module, client, migration_id, timeout_seconds,
                        interval_seconds):
    """Poll GET /v1/migrations/{id} until the migration is terminal.

    ⛔ NOT GET /v1/vms/{id}/migration. That one answers the VM's ACTIVE
    migration and 404s the moment it finishes, so a poll waiting for a terminal
    state there would wait out the whole budget on a move that had completed.
    """
    path = "/v1/migrations/" + quote_path_segment(migration_id)
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            view = client.get(path) or {}
        except ApiError as err:
            fail_json_from_api_error(module, err)
            return None
        if view.get("terminal"):
            return view
        if time.monotonic() >= deadline:
            module.fail_json(
                msg="migration %s did not become terminal within %ds (last "
                    "state: %s)" % (migration_id, timeout_seconds, view.get("state")),
                migration=view,
            )
            return None
        time.sleep(interval_seconds)


def run_module():
    argument_spec = dict(
        vm_id=dict(type="str", required=True),
        dest_host=dict(type="str"),
        auto_place=dict(type="bool", default=False),
        max_bandwidth_mib_s=dict(type="int"),
        vvm_api_url=dict(type="str"),
        vvm_api_token=dict(type="str", no_log=True),
        timeout_seconds=dict(type="int", default=1800),
        poll_interval_seconds=dict(type="int", default=2),
        idempotency_key=dict(type="str", no_log=False),
        validate_certs=dict(type="bool", default=True),
    )
    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
        mutually_exclusive=[("dest_host", "auto_place")],
    )

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
    dest_host = module.params.get("dest_host")

    # One refusal covers both ways of asking for scheduler-chosen placement:
    # auto_place is mutually exclusive with dest_host, so setting it leaves no
    # destination, and a run with neither is the same mistake spelled shorter.
    if not dest_host:
        module.fail_json(msg=_NO_AUTO_PLACE)
        return

    try:
        validate_resource_id(vm_id, "vm_id")
        validate_resource_id(dest_host, "dest_host")
    except ValueError as e:
        module.fail_json(msg=str(e))
        return

    vm_path = "/v1/vms/" + quote_path_segment(vm_id)

    try:
        vm = client.get(vm_path)
    except ApiError as e:
        fail_json_from_api_error(module, e)
        return

    source_host = vm.get("current_host_id")
    if source_host == dest_host:
        module.exit_json(
            changed=False, vm_id=vm_id,
            source_host=source_host, destination_host=dest_host,
        )
        return

    if module.check_mode:
        module.exit_json(
            changed=True, vm_id=vm_id,
            source_host=source_host, destination_host=dest_host,
        )
        return

    try:
        key = derive_idempotency_key(
            MODULE_FQCN, vm_id, "migrate", key_args={"target": dest_host},
            explicit_override=module.params.get("idempotency_key"),
        )
    except ValueError as e:
        module.fail_json(msg=str(e))
        return

    body = {"dest_host_id": dest_host}
    if module.params.get("max_bandwidth_mib_s") is not None:
        body["max_bandwidth_mib_s"] = module.params["max_bandwidth_mib_s"]

    try:
        resp = client.post(vm_path + "/migrate", body, idempotency_key=key) or {}
    except ApiError as e:
        fail_json_from_api_error(module, e)
        return

    migration_id = resp.get("migration_id")
    if not migration_id:
        module.fail_json(msg=_NO_MIGRATION_ID % (vm_id, resp))
        return

    migration = _wait_for_migration(
        module, client, migration_id,
        module.params["timeout_seconds"],
        module.params["poll_interval_seconds"],
    )
    if migration is None:
        return

    if migration.get("state") != "completed":
        module.fail_json(
            msg="migration %s ended in %s, not completed"
                % (migration_id, migration.get("state")),
            migration=migration,
        )
        return

    try:
        vm_after = client.get(vm_path)
    except ApiError as e:
        fail_json_from_api_error(module, e)
        return

    out = dict(
        changed=True, vm_id=vm_id,
        source_host=source_host,
        destination_host=vm_after.get("current_host_id") or dest_host,
        migration_id=migration_id,
        migration=migration,
    )
    # Membership rather than truthiness on purpose: an absent key means the host
    # reported nothing, and inventing a zero for it would claim a measurement
    # that was never taken.
    for field in ("downtime_ms", "precopy_iterations"):
        if field in migration:
            out[field] = migration[field]

    # A zero downtime is dropped, and only the downtime.
    #
    # ⚠️ This module used to return it, on the ground that a measured zero and
    # an absent measurement are different facts. They are — in the control
    # plane's column, which still records both. What is not true today is that a
    # zero arriving here came from a measurement at all: libvirt-control drops
    # libvirt's measured flag and answers 0 for a completion event it never
    # received, and the agent forwards that on every success, so the zeros are
    # mostly unmeasured cutovers wearing a number.
    #
    # The control plane already refuses them twice — they are kept out of its
    # downtime metric and out of its migration endpoint — so returning one here
    # would print a cutover time to an operator that no dashboard and no API
    # response agrees exists. A wall of 0 ms migrations reads as a run of
    # perfect ones, and somebody signs an SLA off against it.
    #
    # Iterations keep their zero: the flag problem is the downtime's alone, and
    # the iteration count has a real fallback.
    if out.get("downtime_ms") == 0:
        del out["downtime_ms"]
    module.exit_json(**out)


def main():
    run_module()


if __name__ == "__main__":
    main()
