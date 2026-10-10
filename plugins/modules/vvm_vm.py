#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 VVM platform team — MIT
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: vvm_vm
short_description: Create or delete a VVM virtual machine.
version_added: "0.2.0"
description:
  - C(state=present) creates the VM when the control plane holds none of that
    C(name), and does nothing when it already does.
  - C(state=absent) deletes it. A name the control plane does not hold is
    C(changed=false), so a playbook is safe to run twice.
  - Supports C(check_mode): the lookup still happens, the write does not.
  - Starting, stopping and restarting a VM is C(vvm_vm_power); moving one is
    C(vvm_vm_migrate). This module is the VM's existence and nothing else.
  - A delete can be refused with C(delete_blocked) while a migration record
    survives it, for up to a few minutes after a move settles. That is the
    control plane refusing, not a transport error, and it is reported as is.
options:
  name:
    description: The VM's name. The module addresses it by name, not by id.
    type: str
    required: true
  state:
    description: C(present) creates it if absent; C(absent) deletes it.
    type: str
    choices: [present, absent]
    default: present
  cluster_id:
    description:
      - Cluster the VM is in. Required for BOTH states: the list route that
        resolves C(name) answers 400 without it.
    type: str
    required: true
  datastore_id:
    description: Datastore the VM's disk lives on. Required for
        C(state=present). Must belong to the same cluster.
    type: str
  network_id:
    description: Guest network the VM is attached to. Required for
        C(state=present). Must belong to the same cluster and must not be
        being deleted.
    type: str
  vcpu_request:
    description: vCPUs. Required for C(state=present).
    type: int
  memory_request_mib:
    description: Memory in MiB. Required for C(state=present).
    type: int
  golden_image:
    description:
      - The golden image to boot from. Required for C(state=present).
      - B(It is a stem, not a filename.) The platform resolves it to
        C(<images>/<golden_image>.qcow2), so passing the file name you can see
        on the datastore asks for C(<name>.qcow2.qcow2) and is refused.
    type: str
  boot_disk_gib:
    description: Boot disk size in GiB. Required for C(state=present).
    type: int
  boot_subnet_id:
    description: VPC subnet the boot disk's address comes from. Required for
        C(state=present).
    type: str
  cloud_init:
    description:
      - cloud-init data handed to the guest verbatim. Keys C(hostname),
        C(ssh_authorized_keys), C(user_data).
      - There is no DHCP and no DNS record, so a guest with keys that reach no
        login user is unreachable. The control plane refuses that body.
      - Marked C(no_log): C(user_data) holds whatever the playbook put there.
      - The control plane DOES publish the stored cloud-init on a VM read, as
        base64, which C(no_log) cannot mask because it is a different string
        from the argument. So the module removes C(cloud_init) from the C(vm) it
        returns, in every state and in check mode, rather than relying on the
        control plane to leave it out.
    type: dict
  encrypted:
    description: Encrypt the boot disk under the VM's project's Key Protect
        root key. Refused when the control plane has no Key Protect instance.
    type: bool
  dr_tier:
    description: DR tier. A tier that implies protection is refused unless the
        VM has protection.
    type: str
  anti_affinity_rule_ids:
    description: Anti-affinity rules the VM joins before it is first placed.
    type: list
    elements: str
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
- name: Ensure a web VM exists
  ibm.vvm.vvm_vm:
    name: web-01
    cluster_id: "{{ vvm_cluster_id }}"
    datastore_id: "{{ vvm_datastore_id }}"
    network_id: "{{ vvm_network_id }}"
    vcpu_request: 2
    memory_request_mib: 4096
    golden_image: ubuntu-2404
    boot_disk_gib: 20
    boot_subnet_id: "{{ vvm_boot_subnet_id }}"
    cloud_init:
      hostname: web-01
      ssh_authorized_keys:
        - "{{ lookup('file', '~/.ssh/id_ed25519.pub') }}"

- name: Delete it again
  ibm.vvm.vvm_vm:
    name: web-01
    cluster_id: "{{ vvm_cluster_id }}"
    state: absent
"""

RETURN = r"""
changed:
  description: True when the VM was created or deleted.
  type: bool
  returned: always
vm:
  description: The VM as the control plane holds it. The row before the delete
      when C(state=absent).
  type: dict
  returned: when the VM exists or existed
job:
  description: The terminal create or delete job.
  type: dict
  returned: when a write was performed and C(wait=true)
"""

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.ibm.vvm.plugins.module_utils.resources import (
    COMMON_ARGS, run_resource_module,
)

MODULE_FQCN = "ibm.vvm.vvm_vm"
COLLECTION = "/v1/vms"

# ⛔ EVERY KEY HERE IS A json TAG OF postVMReq IN the control plane's handler, and
# the unit tests read that struct rather than this list.
CREATE_FIELDS = (
    "cluster_id", "datastore_id", "network_id", "vcpu_request",
    "memory_request_mib", "golden_image", "boot_disk_gib", "boot_subnet_id",
    "cloud_init", "encrypted", "dr_tier", "anti_affinity_rule_ids",
)


def run_module():
    argument_spec = dict(COMMON_ARGS)
    argument_spec.update(
        datastore_id=dict(type="str"),
        network_id=dict(type="str"),
        vcpu_request=dict(type="int"),
        memory_request_mib=dict(type="int"),
        golden_image=dict(type="str"),
        boot_disk_gib=dict(type="int"),
        boot_subnet_id=dict(type="str"),
        # 🔑 no_log, BECAUSE user_data IS WHATEVER THE PLAYBOOK PUT THERE —
        # a password, a token, a private key. Without it Ansible prints the
        # whole dict in the play output and in invocation.module_args, which
        # ends up in every log of the run.
        cloud_init=dict(type="dict", no_log=True),
        encrypted=dict(type="bool"),
        dr_tier=dict(type="str"),
        anti_affinity_rule_ids=dict(type="list", elements="str"),
    )
    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
        required_if=[(
            "state", "present",
            ["datastore_id", "network_id", "vcpu_request",
             "memory_request_mib", "golden_image", "boot_disk_gib",
             "boot_subnet_id"],
        )],
    )

    run_resource_module(module, COLLECTION, MODULE_FQCN, "vm", CREATE_FIELDS)


def main():
    run_module()


if __name__ == "__main__":
    main()
