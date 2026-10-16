# -*- coding: utf-8 -*-
# Copyright (c) 2026 VVM platform team — MIT
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
name: vvm
short_description: VVM dynamic inventory plugin.
description:
  - Queries C(GET /v1/vms) (paged) and emits VMs as hosts in inventory.
  - VM attributes from spec §5.3 are surfaced as host_vars; six built-in
    C(keyed_groups) (cluster, project, region, host, dr_tier,
    paravirt_ready) auto-derive from them.
  - Authentication resolves plugin C(vvm_api_token) then C(VVM_API_TOKEN) env
    then C(~/.vvm/credentials) then C(IBMCLOUD_API_KEY) IAM exchange.
  - Cache is off by default. When enabled, the cache key is namespaced by a
    sha256 prefix of the resolved token so two operators on the same host
    cannot share each other's inventory.
options:
  plugin:
    description: Must be set to C(ibm.vvm.vvm).
    type: str
    required: true
    choices: [ibm.vvm.vvm]
  vvm_api_url:
    description: VVM API base URL. Falls back to C(VVM_API_URL) env.
    type: str
  vvm_api_token:
    description: Bearer token. Falls back to the same chain as the modules.
    type: str
  cluster_id:
    description: Optional VVM cluster id to scope the listing.
    type: str
  project:
    description: Optional project name; VMs whose C(project) attribute does
        not match are skipped.
    type: str
  include_sandbox:
    description: Include VMs whose C(purpose) is C(sandbox-test).
    type: bool
    default: false
extends_documentation_fragment:
  - constructed
  - inventory_cache
"""

EXAMPLES = r"""
plugin: ibm.vvm.vvm
cluster_id: cluster-1
project: payments
keyed_groups:
  - key: cluster_id
    prefix: cluster
  - key: project
    prefix: project
  - key: region
    prefix: region
  - key: current_host_id
    prefix: host
  - key: dr_tier
    prefix: dr
  - key: paravirt_ready
    prefix: paravirt
compose:
  ansible_host: primary_ip
cache: false
"""

import hashlib
import json
import os
import sys
from configparser import ConfigParser

from ansible.errors import AnsibleError
from ansible.module_utils.urls import (
    Request, ConnectionError, SSLValidationError,
)
from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError
from ansible.module_utils.six.moves.urllib.parse import urlencode
from ansible.plugins.inventory import (
    BaseInventoryPlugin, Cacheable, Constructable,
)

IAM_TOKEN_URL = "https://iam.cloud.ibm.com/identity/token"
NO_CREDS_MSG = (
    "No VVM API credentials. Set VVM_API_TOKEN, write ~/.vvm/credentials, "
    "or pass vvm_api_token in the plugin config."
)


def _exchange_api_key_for_iam_token(api_key):
    req = Request(timeout=10)
    body = urlencode({
        "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
        "apikey": api_key,
    })
    resp = req.open(
        method="POST",
        url=IAM_TOKEN_URL,
        data=body.encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    return json.loads(resp.read().decode("utf-8"))["access_token"]


class InventoryModule(BaseInventoryPlugin, Constructable, Cacheable):
    NAME = "ibm.vvm.vvm"

    def verify_file(self, path):
        if not super(InventoryModule, self).verify_file(path):
            return False
        base = os.path.basename(path)
        if base.endswith(("vvm.yml", "vvm.yaml")):
            return True
        return "vvm" in base

    def _resolve_token(self):
        """Mirror of vvm_api.resolve_token, but reads from plugin config first
        (self.get_option) instead of a module argument."""
        explicit = self.get_option("vvm_api_token")
        if explicit:
            return explicit
        env_token = os.environ.get("VVM_API_TOKEN")
        if env_token:
            return env_token
        creds_path = os.path.expanduser("~/.vvm/credentials")
        if os.path.isfile(creds_path):
            cp = ConfigParser()
            cp.read(creds_path)
            if cp.has_option("default", "api_token"):
                return cp.get("default", "api_token")
        api_key = os.environ.get("IBMCLOUD_API_KEY")
        if api_key:
            return _exchange_api_key_for_iam_token(api_key)
        raise AnsibleError(NO_CREDS_MSG)

    def _resolve_api_url(self):
        explicit = self.get_option("vvm_api_url")
        if explicit:
            return explicit.rstrip("/")
        env_url = os.environ.get("VVM_API_URL")
        if env_url:
            return env_url.rstrip("/")
        raise AnsibleError(
            "No VVM API URL. Set VVM_API_URL or pass vvm_api_url in the "
            "plugin config."
        )

    def _list_paged(self, base_url, token, path):
        """Loop GET path with ?cursor= following next_cursor, accumulating
        items. Returns the full list."""
        items = []
        cursor = None
        req = Request(timeout=30)
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "User-Agent": "ibm.vvm-ansible-inventory/0.1.0",
        }
        while True:
            url = base_url + path
            if cursor:
                url += ("&" if "?" in path else "?") + urlencode({"cursor": cursor})
            try:
                resp = req.open(method="GET", url=url, headers=headers)
            except HTTPError as e:
                raise AnsibleError(
                    "VVM API %s returned %d" % (path, e.code)
                )
            except (URLError, ConnectionError, SSLValidationError) as e:
                raise AnsibleError("VVM API connection error: %s" % str(e))
            payload = json.loads(resp.read().decode("utf-8") or "{}")
            items.extend(payload.get("items") or [])
            cursor = payload.get("next_cursor")
            if not cursor:
                return items

    def _cache_key_for(self, path, token):
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
        return self.get_cache_key(path) + "::" + token_hash

    def parse(self, inventory, loader, path, cache=True):
        super(InventoryModule, self).parse(inventory, loader, path)
        self._read_config_data(path)

        token = self._resolve_token()
        base_url = self._resolve_api_url()

        user_cache_setting = self.get_option("cache")
        cache_key = self._cache_key_for(path, token)
        vms = None
        if user_cache_setting and cache:
            try:
                vms = self._cache[cache_key]
                sys.stderr.write(
                    "ibm.vvm.vvm: using cached inventory (key=%s)\n"
                    % cache_key
                )
            except KeyError:
                vms = None

        if vms is None:
            list_path = "/v1/vms"
            cluster_id = self.get_option("cluster_id")
            if cluster_id:
                list_path += "?" + urlencode({"cluster_id": cluster_id})
            vms = self._list_paged(base_url, token, list_path)
            if user_cache_setting:
                self._cache[cache_key] = vms

        project_filter = self.get_option("project")
        include_sandbox = self.get_option("include_sandbox")
        strict = self.get_option("strict")

        for vm in vms:
            if project_filter and vm.get("project") != project_filter:
                continue
            if not include_sandbox and vm.get("purpose") == "sandbox-test":
                continue
            host = vm.get("id")
            if not host:
                continue
            inventory.add_host(host)
            for key, value in vm.items():
                inventory.set_variable(host, key, value)
            self._set_composite_vars(
                self.get_option("compose"), vm, host, strict=strict,
            )
            self._add_host_to_composed_groups(
                self.get_option("groups"), vm, host, strict=strict,
            )
            self._add_host_to_keyed_groups(
                self.get_option("keyed_groups"), vm, host, strict=strict,
            )
