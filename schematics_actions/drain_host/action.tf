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
  description = "Trusted Profile bound to this Action (vvm-host-drain-actor). Action runs AS this TP, not as the invoker."
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
  name              = "vvm-drain-host"
  description       = "Drain a single VVM host for maintenance (host.drain + vm.migrate)."
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

  source_readme_url = "playbooks/drain_host.yml"

  # Bind the Action to vvm-host-drain-actor so it runs with that TP's
  # scoped IAM policy (host.drain + vm.migrate on vvm-cp RG only), NOT
  # the invoker's broader identity. A leaked Schematics-invoker
  # credential cannot escalate past the TP's scope.
  credentials {
    name        = "trusted_profile"
    description = "Trusted Profile vvm-host-drain-actor (host.drain + vm.migrate scope)."
    value       = var.trusted_profile_id
    metadata {
      type = "link"
    }
  }

  action_inputs {
    name  = "host_id"
    value = ""
    metadata {
      type        = "string"
      description = "VVM host identifier (vars_prompt-supplied at run time)."
    }
  }

  bastion {
    name = "none"
  }
}

output "action_id" {
  value = ibm_schematics_action.this.id
}
