# vvm-drain-host Schematics Action

Wraps `playbooks/drain_host.yml`. Drains a single VVM host for maintenance and follows the per-VM live-migration progress to completion.

## Trusted Profile binding

| Policy                      | Scope                                              |
|-----------------------------|----------------------------------------------------|
| Trusted Profile             | `vvm-host-drain-actor`                             |
| IAM role on `vvm-cp` RG     | `Operator`                                         |
| Allowed action verbs        | `host.drain`, `vm.migrate`                         |
| Forbidden                   | All `vm.power`, `vm.backup`, `vm.dr_test` actions  |

Provision the TP via `platform-infra` (see open follow-up issue). The TP's id is the `trusted_profile_id` variable input to this module.

## Inputs

| Name                | Type   | Description                                                                 |
|---------------------|--------|-----------------------------------------------------------------------------|
| `vvm_version_tag`   | string | Git tag of the `ibm.vvm` collection (e.g. `v0.1.0`).                        |
| `trusted_profile_id`| string | Trusted Profile bound to this Action.                                       |
| `resource_group_id` | string | Resource group for the Schematics Action.                                   |

The Action itself prompts for `host_id` at run time (`vars_prompt`).

## Outputs

| Name        | Description                                |
|-------------|--------------------------------------------|
| `action_id` | Schematics Action identifier — pass to operators / dashboards. |

## Apply

```bash
terraform init
terraform apply \
  -var vvm_version_tag=v0.1.0 \
  -var trusted_profile_id=Profile-… \
  -var resource_group_id=…
```
