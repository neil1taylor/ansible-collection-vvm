# ibm.vvm — Ansible Collection for IBM Cloud VPC Virtualization Manager

Operational verbs + dynamic inventory for [VVM](https://github.com/neil1taylor/ansible-collection-vvm). All modules call the VVM API — none reach around it to libvirt, the IBM Cloud SDK, qemu-img, or the host agent.

## Install

The collection is not on Ansible Galaxy. Install it from this repository. In a Schematics Action, put a `collections/requirements.yml` next to the playbook:

```yaml
collections:
  - name: https://github.com/neil1taylor/ansible-collection-vvm.git
    type: git
    version: v0.1.0
```

Outside Schematics:

```bash
ansible-galaxy collection install git+https://github.com/neil1taylor/ansible-collection-vvm.git,v0.1.0
```

## Authentication

The `ApiClient` resolves credentials in this order, stopping at the first hit:

1. **Explicit module arg** — `vvm_api_token: "{{ token }}"`
2. **Env var** — `VVM_API_TOKEN`
3. **Credentials file** — `~/.vvm/credentials` (`[default]` section, `api_token = …`)
4. **Schematics IAM exchange** — `IBMCLOUD_API_KEY` swapped at `https://iam.cloud.ibm.com/identity/token` for a Bearer token

## Idempotency-Key derivation

Every write call sends `Idempotency-Key: <32-hex-chars>` so retries are safe. The key is derived deterministically — operators can reproduce it for an explicit override using exactly this algorithm:

```python
import hashlib, json

payload = {
    "fqcn":   "ibm.vvm.<module>",   # e.g. "ibm.vvm.vvm_vm_power"
    "target": "<resource-id>",      # e.g. "vm-123"
    "state":  "<verb-or-state>",    # e.g. "started"
    "args":   {...},                # the verb's key_args dict (must not include vvm_api_token)
}
key = hashlib.sha256(
    json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()[:32]
```

Override with `idempotency_key: "<key>"` on any module that supports it. The override must match `[A-Za-z0-9_-]{16,128}` so it cannot smuggle CRLF into the `Idempotency-Key` HTTP header; invalid overrides raise `ValueError`.

## Modules

| FQCN                                | Endpoint                              | Verb                      |
| ----------------------------------- | ------------------------------------- | ------------------------- |
| `ibm.vvm.vvm_vm`                    | `POST`/`DELETE /v1/vms`               | create/delete a VM        |
| `ibm.vvm.vvm_network`               | `POST`/`DELETE /v1/networks`          | create/delete a network   |
| `ibm.vvm.vvm_datastore`             | `POST`/`DELETE /v1/datastores`        | create/delete a datastore |
| `ibm.vvm.vvm_vm_power`              | `POST /v1/vms/{id}/power`             | start/stop/reboot/reset   |
| `ibm.vvm.vvm_vm_migrate`            | `POST /v1/vms/{id}/migrate`           | live migrate              |
| `ibm.vvm.vvm_vm_backup`             | `POST /v1/vms/{id}/backup`            | backup                    |
| `ibm.vvm.vvm_vm_dr_test`            | `POST /v1/vms/{id}/dr-test`           | DR test                   |
| `ibm.vvm.vvm_vm_snapshot`           | `POST /v1/vms/{id}/snapshots`         | snapshot                  |
| `ibm.vvm.vvm_host_drain`            | `POST /v1/hosts/{id}/drain`           | drain                     |
| `ibm.vvm.vvm_host_drain_cancel`     | `POST /v1/hosts/{id}/drain/cancel`    | cancel drain              |

`vvm_vm`, `vvm_network` and `vvm_datastore` are the declarative three: they take
`state: present` or `state: absent`, address the resource by `name` rather than
by id, and support `check_mode`. Each write is asynchronous — the `POST` and the
`DELETE` answer 202 with a `job_id` — so with `wait: true` (the default) the
module polls `GET /v1/jobs/{id}` and fails unless the job succeeded.

## Inventory plugin

`ibm.vvm.vvm` queries `/v1/vms` + `/v1/hosts` and emits hosts grouped by `cluster`, `project`, `region`, `host`, `dr_tier`, and `paravirt_ready`. See `playbooks/inventory.vvm.yml.example` for usage.

