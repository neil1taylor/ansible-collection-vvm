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
  description = "Trusted Profile bound to this Action (vvm-dr-test-actor). Action runs AS this TP, not as the invoker."
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
  name              = "vvm-quarterly-dr-test"
  description       = "Quarterly DR test of the payments project (vm.dr_test only — no live writes)."
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

  source_readme_url = "playbooks/quarterly_dr_test.yml"

  # Bind to vvm-dr-test-actor (vm.dr_test scope ONLY). A leaked invoker
  # credential cannot mutate production state via this Action.
  credentials {
    name        = "trusted_profile"
    description = "Trusted Profile vvm-dr-test-actor (vm.dr_test scope only — no live writes)."
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
