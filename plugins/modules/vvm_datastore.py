#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 VVM platform team — MIT
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: vvm_datastore
short_description: Create or delete a VVM datastore.
version_added: "0.2.0"
description:
  - C(state=present) creates the datastore when the control plane holds none of
    that C(name), and does nothing when it already does.
  - C(state=absent) deletes it. A name the control plane does not hold is
    C(changed=false), so a playbook is safe to run twice.
  - Supports C(check_mode): the lookup still happens, the write does not.
  - Two modes, as C(POST /v1/datastores) has: give C(size_gib) to have VVM
    provision a new file share, or C(file_share_id) to adopt one that exists.
    They are mutually exclusive and the control plane refuses both together.
  - C(mount_path) is required in BOTH modes. Without it the row is written, the
    mount is fanned out to every Ready host, and every one of them fails on
    arrival.
  - Growing a datastore is C(POST /v1/datastores/{id}/resize) and is not this
    module. A file share cannot be shrunk at all.
options:
  name:
    description: The datastore's name. The module addresses it by name, not by id.
    type: str
    required: true
  state:
    description: C(present) creates it if absent; C(absent) deletes it.
    type: str
    choices: [present, absent]
    default: present
  cluster_id:
    description:
      - Cluster the datastore is in. Required for BOTH states: the list route
        that resolves C(name) answers 400 without it.
    type: str
    required: true
  zone:
    description: IBM Cloud zone the share lives in. Required for C(state=present).
    type: str
  mount_path:
    description: Where every host mounts it. Required for C(state=present), in
        both the provision and the adopt mode.
    type: str
  size_gib:
    description: Size of the share to provision. Mutually exclusive with
        C(file_share_id).
    type: int
  file_share_id:
    description: An existing IBM Cloud file share to adopt. Mutually exclusive
        with C(size_gib).
    type: str
  capacity_gb:
    description: Recorded capacity for an adopted share.
    type: int
  storage_class:
    description: The share's storage class.
    type: str
  iops:
    description: Provisioned IOPS. Omit to accept the platform default, which
        is derived from the size.
    type: int
  encryption_kp_key_id:
    description: Key Protect key the share is encrypted with.
    type: str
  wait:
    description: Poll the create or delete job until it is terminal.
    type: bool
    default: true
  timeout_seconds:
    description: Max seconds to wait for the job.
    type: int
    default: 1800
  poll_interval_seconds:
    description: Seconds between job polls.
    type: int
    default: 5
  vvm_api_url:
    description: VVM API base URL. Falls back to VVM_API_URL env var.
    type: str
  vvm_api_token:
    description: Bearer token. Falls back to VVM_API_TOKEN env,
        C(~/.vvm/credentials), then C(IBMCLOUD_API_KEY) → IAM exchange.
    type: str
  validate_certs:
    description: Verify TLS certificates on the VVM API.
    type: bool
    default: true
author:
  - VVM platform team (@vvm)
"""

EXAMPLES = r"""
- name: Provision a datastore
  ibm.vvm.vvm_datastore:
    name: gold-store
    cluster_id: "{{ vvm_cluster_id }}"
    zone: eu-gb-1
    mount_path: /mnt/vvm/gold
    size_gib: 500
    storage_class: general-purpose
    iops: 4000

- name: Adopt a share somebody else made
  ibm.vvm.vvm_datastore:
    name: adopted-store
    cluster_id: "{{ vvm_cluster_id }}"
    zone: eu-gb-1
    mount_path: /mnt/vvm/adopted
    file_share_id: "{{ share_id }}"

- name: Remove it again
  ibm.vvm.vvm_datastore:
    name: gold-store
    cluster_id: "{{ vvm_cluster_id }}"
    state: absent
"""

RETURN = r"""
changed:
  description: True when the datastore was created or deleted.
  type: bool
  returned: always
datastore:
  description: The datastore as the control plane holds it. The row before the
      delete when C(state=absent).
  type: dict
  returned: when the datastore exists or existed
job:
  description: The terminal create or delete job.
  type: dict
  returned: when a write was performed and C(wait=true)
"""

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.ibm.vvm.plugins.module_utils.resources import (
    COMMON_ARGS, run_resource_module,
)

MODULE_FQCN = "ibm.vvm.vvm_datastore"
COLLECTION = "/v1/datastores"

# ⛔ EVERY KEY HERE IS A json TAG OF postDatastoreRequest IN
# the control plane's handler, and the unit tests read that struct rather
# than this list.
CREATE_FIELDS = (
    "cluster_id", "zone", "mount_path", "size_gib", "file_share_id",
    "capacity_gb", "storage_class", "iops", "encryption_kp_key_id",
)


def run_module():
    argument_spec = dict(COMMON_ARGS)
    argument_spec.update(
        zone=dict(type="str"),
        mount_path=dict(type="str"),
        size_gib=dict(type="int"),
        file_share_id=dict(type="str"),
        capacity_gb=dict(type="int"),
        storage_class=dict(type="str"),
        iops=dict(type="int"),
        encryption_kp_key_id=dict(type="str"),
    )
    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
        mutually_exclusive=[("size_gib", "file_share_id")],
        required_if=[
            ("state", "present", ["zone", "mount_path"]),
            ("state", "present", ["size_gib", "file_share_id"], True),
        ],
    )

    run_resource_module(module, COLLECTION, MODULE_FQCN, "datastore", CREATE_FIELDS)


def main():
    run_module()


if __name__ == "__main__":
    main()
