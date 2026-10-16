#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 VVM platform team — MIT
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: vvm_network
short_description: Create or delete a VVM guest network.
version_added: "0.2.0"
description:
  - C(state=present) creates the guest network when the control plane holds no
    network of that C(name), and does nothing when it already does.
  - C(state=absent) deletes it. A name the control plane does not hold is
    C(changed=false), so a playbook is safe to run twice.
  - Supports C(check_mode): the lookup still happens, the write does not.
  - The create is asynchronous. C(POST /v1/networks) answers 202 with a
    C(job_id); with C(wait=true) (the default) the module polls
    C(GET /v1/jobs/{id}) until the job is terminal and fails unless it
    succeeded, then reads the network back.
  - All calls go through the VVM API; nothing here touches OVN or the IBM Cloud
    SDK.
options:
  name:
    description: The network's name. The module addresses it by name, not by id.
    type: str
    required: true
  state:
    description: C(present) creates it if absent; C(absent) deletes it.
    type: str
    choices: [present, absent]
    default: present
  cluster_id:
    description:
      - Cluster the network is in. Required for BOTH states: the list route
        that resolves C(name) answers 400 without it.
    type: str
    required: true
  vlan_id:
    description: VLAN the network is carried on. Required for C(state=present).
    type: int
  vpc_subnet_id:
    description: IBM Cloud VPC subnet the network is backed by. Required for
        C(state=present).
    type: str
  cidr:
    description: The guest CIDR. Required for C(state=present).
      - B(It must not contain a host's management address or a datastore's
        mount target.) The control plane refuses such a CIDR with 422, because a
        guest network that overlaps one takes the host off the network
        permanently.
    type: str
  ipam_range_start:
    description: First address VVM may hand to a guest. Required for
        C(state=present).
    type: str
  ipam_range_end:
    description: Last address VVM may hand to a guest. Required for
        C(state=present).
    type: str
  ovn_logical_switch_id:
    description: An existing OVN logical switch to adopt instead of creating one.
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
- name: Ensure the web tier's guest network exists
  ibm.vvm.vvm_network:
    name: web-tier
    cluster_id: "{{ vvm_cluster_id }}"
    vlan_id: 101
    vpc_subnet_id: "{{ vvm_vpc_subnet_id }}"
    cidr: 192.168.50.0/24
    ipam_range_start: 192.168.50.10
    ipam_range_end: 192.168.50.200

- name: Remove it again
  ibm.vvm.vvm_network:
    name: web-tier
    cluster_id: "{{ vvm_cluster_id }}"
    state: absent
"""

RETURN = r"""
changed:
  description: True when the network was created or deleted.
  type: bool
  returned: always
network:
  description: The network as the control plane holds it. The row before the
      delete when C(state=absent).
  type: dict
  returned: when the network exists or existed
job:
  description: The terminal create or delete job.
  type: dict
  returned: when a write was performed and C(wait=true)
"""

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.ibm.vvm.plugins.module_utils.resources import (
    COMMON_ARGS, run_resource_module,
)

MODULE_FQCN = "ibm.vvm.vvm_network"
COLLECTION = "/v1/networks"

# ⛔ EVERY KEY HERE IS A json TAG OF postNetworkRequest IN
# the control plane's handler, and the unit tests read that struct rather
# than this list. A name that is not one of its tags is silently dropped by the
# handler's decoder, which is that earlier defect: the call looks right and the
# resource is made wrong, or not at all.
CREATE_FIELDS = (
    "cluster_id", "vlan_id", "vpc_subnet_id", "cidr",
    "ipam_range_start", "ipam_range_end", "ovn_logical_switch_id",
)


def run_module():
    argument_spec = dict(COMMON_ARGS)
    argument_spec.update(
        vlan_id=dict(type="int"),
        vpc_subnet_id=dict(type="str"),
        cidr=dict(type="str"),
        ipam_range_start=dict(type="str"),
        ipam_range_end=dict(type="str"),
        ovn_logical_switch_id=dict(type="str"),
    )
    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
        required_if=[(
            "state", "present",
            ["vlan_id", "vpc_subnet_id", "cidr",
             "ipam_range_start", "ipam_range_end"],
        )],
    )

    run_resource_module(module, COLLECTION, MODULE_FQCN, "network", CREATE_FIELDS)


def main():
    run_module()


if __name__ == "__main__":
    main()
