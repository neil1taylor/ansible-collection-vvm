#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 VVM platform team — MIT
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: vvm_backup
short_description: Manage VVM backup policies, protection groups, and trigger on-demand backups.
version_added: "0.1.0"
description:
  - Idempotently creates or updates a backup policy and/or protection group
    (C(state=present)), then optionally triggers an immediate on-demand backup
    run via C(POST /v1/protection-groups/{id}/backup-now).
  - When C(state=backup) (default), looks up the named or ID-addressed protection
    group and fires C(backup-now). The C(wait) parameter controls whether the
    module blocks until all per-VM jobs reach a terminal state.
  - Implements the C(vvm.platform.vvm_backup) Ansible verb from spec §8.4.
  - All calls go through the VVM API; no direct contact with the host agent,
    libvirt, or COS.
options:
  state:
    description:
      - C(backup) — trigger an on-demand backup on an existing protection group.
        The group must exist; use C(state=present) first if you want declarative
        create-then-backup.
      - C(present) — ensure the backup policy and protection group described by
        C(policy) and C(protection_group_spec) exist, creating or updating them.
        Does not trigger a backup run unless C(backup_after=true).
    type: str
    choices: [backup, present]
    default: backup
  protection_group:
    description:
      - Name (for C(state=backup)) or UUID (for either state) of the protection
        group to act on. Required for C(state=backup). For C(state=present) the
        name is taken from C(protection_group_spec.name) if not supplied here.
    type: str
  policy:
    description:
      - Declarative spec for the backup policy (required for C(state=present)).
        Idempotent — if a policy with the same name already exists it will be
        updated only when a field changes.
      - Keys mirror the C(POST /v1/backup-policies) body: C(name),
        C(schedule_cron), C(retain_days), C(synthetic_full_cadence_hours),
        C(skew_policy), C(cos_bucket), C(cos_region).
      - C(synthetic_full_cadence_hours) is how old a chain's base may get, in
        hours (at least 1), before the next scheduled run makes a synthetic
        full. The older C(synthetic_full_cadence_days) is still accepted and
        taken as that many times 24 hours; if both are given they must agree.
        Neither given means 168 hours (7 days).
      - B(C(retain_days) sets a WORM lock, not a pruning schedule.) The value is
        written onto every object the policy produces as an object-lock
        retain-until date against a COMPLIANCE-mode bucket. Those objects cannot
        be deleted by anyone until the date passes - not with account root
        credentials, and not by removing the policy, the protection group or the
        VM - and COMPLIANCE mode has no early release, so the retention cannot be
        shortened or cancelled afterwards. Lowering the value affects only
        objects written after the change, and the storage is billable for the
        whole period. Seven-year regulatory retention (SEC 17a-4, SOX) is 2555.
    type: dict
  protection_group_spec:
    description:
      - Declarative spec for the protection group (required for C(state=present)).
        Keys mirror C(POST /v1/protection-groups): C(name), C(consistency_model),
        C(selector_kind), C(selector_value). C(policy_id) is derived from the
        C(policy) result; supply C(policy_id) directly when C(policy) is omitted.
    type: dict
  backup_after:
    description:
      - When C(true) and C(state=present), trigger an on-demand backup after
        the policy and group are ensured. Equivalent to chaining C(state=present)
        then C(state=backup) in one task.
    type: bool
    default: false
  wait:
    description:
      - When C(true) and a backup is triggered, poll C(GET /v1/jobs/{id}) for
        every per-VM job until they all reach a terminal state.
    type: bool
    default: false
  timeout_seconds:
    description: Max seconds to wait for all jobs to reach terminal state.
    type: int
    default: 3600
  poll_interval_seconds:
    description: Seconds between job-status polls.
    type: int
    default: 5
  vvm_api_url:
    description: VVM API base URL. Falls back to VVM_API_URL env var.
    type: str
  vvm_api_token:
    description: Bearer token. Falls back to VVM_API_TOKEN env, C(~/.vvm/credentials),
        then C(IBMCLOUD_API_KEY) → IAM exchange.
    type: str
    no_log: true
  validate_certs:
    description: Verify TLS certificates on the VVM API.
    type: bool
    default: true
author:
  - VVM platform team (@vvm)
"""

EXAMPLES = r"""
# Trigger an on-demand backup on the "payments" protection group and wait.
- name: Back up the payments group before migration
  ibm.vvm.vvm_backup:
    protection_group: payments
    wait: true

# Ensure a policy + group exist, then trigger a backup.
- name: Ensure gold tier backup config and run a backup
  ibm.vvm.vvm_backup:
    state: present
    policy:
      name: weekly_full_daily_inc
      schedule_cron: "0 2 * * *"
      retain_days: 35
      synthetic_full_cadence_hours: 168
      skew_policy: flag-and-keep
      cos_bucket: vvm-backup-eu-gb
      cos_region: eu-gb
    protection_group_spec:
      name: gold
      consistency_model: per-vm-only
      selector_kind: tag
      selector_value:
        tag: "tier:gold"
    backup_after: true
    wait: true

# Declarative create/update only (no backup run).
- name: Ensure backup policy exists
  ibm.vvm.vvm_backup:
    state: present
    policy:
      name: daily_incremental
      schedule_cron: "0 3 * * *"
      retain_days: 14
      synthetic_full_cadence_days: 7
      skew_policy: flag-and-keep
      cos_bucket: vvm-backup-eu-gb
      cos_region: eu-gb
"""

RETURN = r"""
changed:
  description: True when a resource was created, updated, or a backup was triggered.
  type: bool
  returned: always
state:
  description: The C(state) the module operated in (C(backup) or C(present)).
  type: str
  returned: always
policy:
  description: The backup policy as returned by the API after create/update.
      Present only when C(state=present) and a policy was specified.
  type: dict
  returned: when state=present and policy arg supplied
protection_group:
  description: The protection group as returned by the API after create/update
      or as resolved by name/id for a backup trigger.
  type: dict
  returned: when a group was resolved or created
backup_now:
  description: The C(backup-now) response body, including C(job_ids).
      Present only when a backup was triggered.
  type: dict
  returned: when a backup was triggered
job_results:
  description: Terminal job envelopes keyed by job_id.
      Present only when C(wait=true) and a backup was triggered.
  type: dict
  returned: when wait=true and backup triggered
"""

import time

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.ibm.vvm.plugins.module_utils.vvm_api import (
    ApiClient, ApiError, resolve_api_url, resolve_token,
)
from ansible_collections.ibm.vvm.plugins.module_utils.errors import (
    fail_json_from_api_error,
)
from ansible_collections.ibm.vvm.plugins.module_utils.idempotency import (
    derive_idempotency_key,
)

MODULE_FQCN = "ibm.vvm.vvm_backup"

# HTTP 501 means the backup-now endpoint exists but the server hasn't wired
# the work-channel emitter — surface a clear message.
_BACKUP_NOW_NOT_WIRED_MSG = (
    "POST /v1/protection-groups/{id}/backup-now returned 501: the VVM API "
    "server has not wired the work-channel emitter for this deployment. "
    "Ensure the vvm-backup service is running and connected to the same DB."
)


def _find_group_by_name(client, name):
    """Return the first protection group whose name matches, or None."""
    try:
        resp = client.get("/v1/protection-groups")
    except ApiError:
        return None
    for item in (resp or {}).get("items", []):
        if item.get("name") == name:
            return item
    # Paginate if there are more pages.
    cursor = (resp or {}).get("next_cursor")
    while cursor:
        try:
            resp = client.get("/v1/protection-groups?cursor=" + cursor)
        except ApiError:
            break
        for item in (resp or {}).get("items", []):
            if item.get("name") == name:
                return item
        cursor = (resp or {}).get("next_cursor")
    return None


def _find_policy_by_name(client, name):
    """Return the first backup policy whose name matches, or None."""
    try:
        resp = client.get("/v1/backup-policies")
    except ApiError:
        return None
    for item in (resp or {}).get("items", []):
        if item.get("name") == name:
            return item
    cursor = (resp or {}).get("next_cursor")
    while cursor:
        try:
            resp = client.get("/v1/backup-policies?cursor=" + cursor)
        except ApiError:
            break
        for item in (resp or {}).get("items", []):
            if item.get("name") == name:
                return item
        cursor = (resp or {}).get("next_cursor")
    return None


def _ensure_policy(module, client, policy_spec):
    """Idempotently create or update the backup policy. Returns (policy_dict, changed)."""
    existing = _find_policy_by_name(client, policy_spec["name"])
    if existing:
        # Check for meaningful drift on the mutable fields.
        drift = {}
        for field in ("schedule_cron", "retain_days", "synthetic_full_cadence_hours",
                      "synthetic_full_cadence_days", "skew_policy", "cos_bucket", "cos_region"):
            if field in policy_spec and existing.get(field) != policy_spec[field]:
                drift[field] = (existing.get(field), policy_spec[field])
        if not drift:
            return existing, False
        # PUT with If-Match to update.
        if module.check_mode:
            return existing, True
        try:
            key = derive_idempotency_key(MODULE_FQCN, existing["id"], "update-policy", {
                "name": policy_spec["name"],
            })
            body = dict(policy_spec)
            updated = client._send(
                "PUT",
                "/v1/backup-policies/" + existing["id"],
                body=body,
                extra_headers={
                    "If-Match": existing["updated_at"],
                    "Idempotency-Key": key,
                },
            )
            return updated, True
        except ApiError as e:
            fail_json_from_api_error(module, e)
    # Create.
    if module.check_mode:
        return {"name": policy_spec["name"]}, True
    try:
        key = derive_idempotency_key(MODULE_FQCN, policy_spec["name"], "create-policy", {})
        created = client.post("/v1/backup-policies", policy_spec, idempotency_key=key)
        return created, True
    except ApiError as e:
        fail_json_from_api_error(module, e)


def _ensure_group(module, client, group_spec, policy_id):
    """Idempotently create or update the protection group. Returns (group_dict, changed)."""
    existing = _find_group_by_name(client, group_spec["name"])
    if existing:
        # Drift check: policy_id, consistency_model, selector_kind.
        drift = {}
        if policy_id and existing.get("policy_id") != policy_id:
            drift["policy_id"] = (existing["policy_id"], policy_id)
        for field in ("consistency_model", "selector_kind"):
            if field in group_spec and existing.get(field) != group_spec[field]:
                drift[field] = (existing.get(field), group_spec[field])
        if not drift:
            return existing, False
        if module.check_mode:
            return existing, True
        try:
            body = dict(group_spec)
            if policy_id:
                body["policy_id"] = policy_id
            if isinstance(body.get("selector_value"), dict):
                body["selector_value"] = body["selector_value"]
            key = derive_idempotency_key(MODULE_FQCN, existing["id"], "update-group", {
                "name": group_spec["name"],
            })
            updated = client._send(
                "PUT",
                "/v1/protection-groups/" + existing["id"],
                body=body,
                extra_headers={
                    "If-Match": existing["updated_at"],
                    "Idempotency-Key": key,
                },
            )
            return updated, True
        except ApiError as e:
            fail_json_from_api_error(module, e)
    # Create.
    if module.check_mode:
        return {"name": group_spec["name"]}, True
    try:
        body = dict(group_spec)
        if policy_id:
            body["policy_id"] = policy_id
        key = derive_idempotency_key(MODULE_FQCN, group_spec["name"], "create-group", {})
        created = client.post("/v1/protection-groups", body, idempotency_key=key)
        return created, True
    except ApiError as e:
        fail_json_from_api_error(module, e)


def _trigger_backup_now(module, client, group_id):
    """Fire POST /v1/protection-groups/{id}/backup-now. Returns backup-now response."""
    if module.check_mode:
        return {"protection_group_id": group_id, "jobs_emitted": 0, "job_ids": []}
    key = derive_idempotency_key(MODULE_FQCN, group_id, "backup-now", {})
    try:
        resp = client.post(
            "/v1/protection-groups/" + group_id + "/backup-now",
            {},
            idempotency_key=key,
        )
        return resp
    except ApiError as e:
        if e.status == 501:
            module.fail_json(msg=_BACKUP_NOW_NOT_WIRED_MSG, group_id=group_id)
        fail_json_from_api_error(module, e)


def _wait_for_jobs(module, client, job_ids, timeout_seconds, poll_interval_seconds):
    """Poll each job until terminal. Returns dict of {job_id: terminal_envelope}."""
    if not job_ids:
        return {}
    deadline = time.time() + timeout_seconds
    results = {}
    pending = list(job_ids)
    while pending:
        if time.time() > deadline:
            module.fail_json(
                msg="Timed out waiting for backup jobs after %ds. "
                    "Still pending: %s" % (timeout_seconds, pending),
                pending_job_ids=pending,
                completed=results,
            )
        for jid in list(pending):
            try:
                job = client.get_job(jid)
            except ApiError:
                # Transient — keep polling unless timed out.
                continue
            state = job.get("state", "")
            if state in ("succeeded", "failed", "cancelled"):
                results[jid] = job
                pending.remove(jid)
        if pending:
            time.sleep(poll_interval_seconds)
    return results


def run_module():
    argument_spec = dict(
        state=dict(type="str", choices=["backup", "present"], default="backup"),
        protection_group=dict(type="str"),
        policy=dict(type="dict"),
        protection_group_spec=dict(type="dict"),
        backup_after=dict(type="bool", default=False),
        wait=dict(type="bool", default=False),
        timeout_seconds=dict(type="int", default=3600),
        poll_interval_seconds=dict(type="int", default=5),
        vvm_api_url=dict(type="str"),
        vvm_api_token=dict(type="str", no_log=True),
        validate_certs=dict(type="bool", default=True),
    )
    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
        required_if=[
            ("state", "backup", ["protection_group"]),
            ("state", "present", ["policy", "protection_group_spec"], True),
        ],
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

    state = module.params["state"]
    changed = False
    result = dict(changed=False, state=state)

    if state == "present":
        # --- Declarative mode: ensure policy and/or group ---
        policy_spec = module.params.get("policy")
        group_spec = module.params.get("protection_group_spec")
        policy_id = None

        if policy_spec:
            if "name" not in policy_spec:
                module.fail_json(msg="policy.name is required")
                return
            policy_obj, pol_changed = _ensure_policy(module, client, policy_spec)
            result["policy"] = policy_obj
            if pol_changed:
                changed = True
            policy_id = (policy_obj or {}).get("id")

        if group_spec:
            if "name" not in group_spec:
                module.fail_json(msg="protection_group_spec.name is required")
                return
            # Allow explicit policy_id in group_spec as an override.
            effective_policy_id = group_spec.get("policy_id") or policy_id
            if not effective_policy_id:
                module.fail_json(
                    msg="protection_group_spec.policy_id or a 'policy' block is required "
                        "to resolve the policy for the group"
                )
                return
            group_obj, grp_changed = _ensure_group(module, client, group_spec, effective_policy_id)
            result["protection_group"] = group_obj
            if grp_changed:
                changed = True

        # Optional: fire backup-now after ensuring state.
        if module.params["backup_after"] and group_spec:
            group_id = (result.get("protection_group") or {}).get("id")
            if group_id:
                backup_resp = _trigger_backup_now(module, client, group_id)
                result["backup_now"] = backup_resp
                changed = True
                if module.params["wait"] and backup_resp:
                    job_ids = backup_resp.get("job_ids") or []
                    job_results = _wait_for_jobs(
                        module, client, job_ids,
                        module.params["timeout_seconds"],
                        module.params["poll_interval_seconds"],
                    )
                    result["job_results"] = job_results
                    # Fail if any job ended in non-succeeded.
                    failed_jobs = {jid: j for jid, j in job_results.items()
                                   if j.get("state") != "succeeded"}
                    if failed_jobs:
                        module.fail_json(
                            msg="%d backup job(s) did not succeed" % len(failed_jobs),
                            failed_jobs=failed_jobs,
                            **result
                        )

    else:
        # --- Trigger mode: fire backup-now on an existing protection group ---
        pg_ref = module.params["protection_group"]

        # Resolve by name or id.
        group_obj = None
        try:
            # Try ID first (if it looks like a UUID).
            group_obj = client.get("/v1/protection-groups/" + pg_ref)
        except ApiError as e:
            if e.status == 404 or e.status == 400:
                # Not found by ID — try by name.
                group_obj = _find_group_by_name(client, pg_ref)
            else:
                fail_json_from_api_error(module, e)

        if not group_obj:
            module.fail_json(
                msg="protection_group %r not found by name or id" % pg_ref,
                protection_group=pg_ref,
            )
            return

        result["protection_group"] = group_obj
        group_id = group_obj["id"]

        backup_resp = _trigger_backup_now(module, client, group_id)
        result["backup_now"] = backup_resp
        changed = True

        if module.params["wait"] and backup_resp:
            job_ids = backup_resp.get("job_ids") or []
            job_results = _wait_for_jobs(
                module, client, job_ids,
                module.params["timeout_seconds"],
                module.params["poll_interval_seconds"],
            )
            result["job_results"] = job_results
            failed_jobs = {jid: j for jid, j in job_results.items()
                           if j.get("state") != "succeeded"}
            if failed_jobs:
                module.fail_json(
                    msg="%d backup job(s) did not succeed" % len(failed_jobs),
                    failed_jobs=failed_jobs,
                    **result
                )

    result["changed"] = changed
    module.exit_json(**result)


def main():
    run_module()


if __name__ == "__main__":
    main()
