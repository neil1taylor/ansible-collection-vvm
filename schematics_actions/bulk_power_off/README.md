# vvm-bulk-power-off Schematics Action

Wraps `playbooks/bulk_power_off.yml`. Walks the `project_payments` dynamic-inventory group five VMs at a time, requesting guest shutdown then falling back to hard stop when the guest agent fails.

## Trusted Profile binding

| Policy                      | Scope                                              |
|-----------------------------|----------------------------------------------------|
| Trusted Profile             | `vvm-bulk-power-actor`                             |
| IAM role on `vvm-cp` RG     | `Operator`                                         |
| Allowed action verbs        | `vm.power` only                                    |
| Forbidden                   | `vm.migrate`, `vm.backup`, `vm.dr_test`, `host.*`  |

The narrow `vm.power`-only scope means a leaked TP credential cannot trigger anything beyond a power state change.

## Inputs

| Name                | Type   | Description                                                                 |
|---------------------|--------|-----------------------------------------------------------------------------|
| `vvm_version_tag`   | string | Git tag of the `ibm.vvm` collection (e.g. `v0.1.0`).                        |
| `trusted_profile_id`| string | Trusted Profile bound to this Action.                                       |
| `resource_group_id` | string | Resource group for the Schematics Action.                                   |

## Outputs

| Name        | Description                                                |
|-------------|------------------------------------------------------------|
| `action_id` | Schematics Action identifier — pass to operators / dashboards. |

## Apply

```bash
terraform init
terraform apply \
  -var vvm_version_tag=v0.1.0 \
  -var trusted_profile_id=Profile-… \
  -var resource_group_id=…
```
