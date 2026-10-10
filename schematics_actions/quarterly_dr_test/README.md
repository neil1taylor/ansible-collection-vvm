# vvm-quarterly-dr-test Schematics Action

Wraps `playbooks/quarterly_dr_test.yml`. Walks the `project_payments` dynamic-inventory group and runs a SandboxBoot DR test against each VM's latest recovery point. Surfaces per-VM `probe_ok` + `sandbox_vm_id` via `set_stats` for the Schematics run summary.

## Trusted Profile binding

| Policy                      | Scope                                            |
|-----------------------------|--------------------------------------------------|
| Trusted Profile             | `vvm-dr-test-actor`                              |
| IAM role on `vvm-cp` RG     | `Operator`                                       |
| Allowed action verbs        | `vm.dr_test` ONLY                                |
| Forbidden                   | Every live-write verb (`vm.power`, `vm.migrate`, `vm.backup`, `host.*`) |

This is the lowest-blast-radius TP of the three. A DR test cannot mutate production state by design (SandboxBoot runs against a copy with auto-teardown).

## Inputs

| Name                | Type   | Description                                                                 |
|---------------------|--------|-----------------------------------------------------------------------------|
| `vvm_version_tag`   | string | Git tag of the `ibm.vvm` collection (e.g. `v0.1.0`).                        |
| `trusted_profile_id`| string | Trusted Profile bound to this Action.                                       |
| `resource_group_id` | string | Resource group for the Schematics Action.                                   |

## Outputs

| Name        | Description                                                  |
|-------------|--------------------------------------------------------------|
| `action_id` | Schematics Action identifier — pass to operators / dashboards. |

## Apply

```bash
terraform init
terraform apply \
  -var vvm_version_tag=v0.1.0 \
  -var trusted_profile_id=Profile-… \
  -var resource_group_id=…
```
