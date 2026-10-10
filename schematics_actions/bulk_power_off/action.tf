terraform {
  required_providers {
    ibm = {
      source  = "IBM-Cloud/ibm"
      version = "~> 1.70"
    }
  }
}

variable "vvm_version_tag" {
  description = "Semver tag of the ibm.vvm collection (e.g. v0.1.0). Branches and arbitrary refs are rejected."
  type        = string

  validation {
    condition     = can(regex("^v[0-9]+\\.[0-9]+\\.[0-9]+$", var.vvm_version_tag))
    error_message = "vvm_version_tag must be an immutable semver tag like v0.1.0, not a branch or arbitrary ref."
  }
}

variable "trusted_profile_id" {
  description = "Trusted Profile bound to this Action (vvm-bulk-power-actor). Action runs AS this TP, not as the invoker."
  type        = string

  validation {
    condition     = length(var.trusted_profile_id) > 0
    error_message = "trusted_profile_id must be set so the Action runs under the constrained TP, not the invoker's IAM identity."
  }
}

variable "resource_group_id" {
  description = "Resource group for the Schematics Action."
  type        = string

  validation {
    condition     = length(var.resource_group_id) > 0
    error_message = "resource_group_id must be set."
  }
}

resource "ibm_schematics_action" "this" {
  name              = "vvm-bulk-power-off"
  description       = "Bulk power off VMs in the payments project (vm.power scope only)."
  location          = "eu-gb"
  resource_group    = var.resource_group_id
  source_type       = "GitHub"
  command_parameter = "ansible-playbook"

  source {
    source_type = "GitHub"
    git {
      computed_git_repo_url = "https://github.com/neil1taylor/ansible-collection-vvm"
      git_branch            = var.vvm_version_tag
    }
  }

  source_readme_url = "playbooks/bulk_power_off.yml"

  # Bind to vvm-bulk-power-actor (vm.power scope only). A leaked
  # invoker credential cannot escalate past vm.power on the bound RG.
  credentials {
    name        = "trusted_profile"
    description = "Trusted Profile vvm-bulk-power-actor (vm.power scope)."
    value       = var.trusted_profile_id
    metadata {
      type = "link"
    }
  }

  bastion {
    name = "none"
  }
}

output "action_id" {
  value = ibm_schematics_action.this.id
}
