# -*- coding: utf-8 -*-
# Copyright (c) 2026 VVM platform team — MIT
"""state=present / state=absent for a VVM lifecycle resource.

A VM, a network and a datastore are created and deleted the same way, and it is
the way nothing else in this collection does: the write answers 202 with an
C(id) and a C(job_id) rather than the resource, a DELETE is refused without an
C(If-Match), and neither is finished until its job reaches C(succeeded). Written
once per module that would be three copies of that sequence, and a module that
got one step of it wrong would look exactly like one that got it right.

⛔ C(check_mode) RETURNS BEFORE THE WRITE, NEVER BEFORE THE READ. The lookup
that decides whether anything has to change still happens — a check run that
guessed would report a change for a resource that already exists.
"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

from ansible.module_utils.six.moves.urllib.parse import quote

from ansible_collections.ibm.vvm.plugins.module_utils.vvm_api import (
    ApiClient, ApiError, resolve_api_url, resolve_token,
)
from ansible_collections.ibm.vvm.plugins.module_utils.errors import (
    fail_json_from_api_error,
)
from ansible_collections.ibm.vvm.plugins.module_utils.idempotency import (
    derive_idempotency_key,
)

# Fields of a resource row that carry what the caller put in, not what the
# control plane decided.
#
# 🚨 cloud_init IS THE ONE, AND no_log ON THE ARGUMENT DOES NOT COVER IT. A VM
# read publishes the stored cloud-init under the same name — store.Vm.CloudInit
# is a []byte, so GET /v1/vms/{id} and every list render it as BASE64, and its
# user_data is wherever a playbook put a password, a join token or a registry
# credential. Ansible masks the argument's own value, and the returned string is
# a different one (the base64), so it comes straight back out in the task
# result, in `register:`, and in anything that logs it — a Schematics Action's
# log is kept. The module docs once said the control plane does not publish it
# on a read; it does.
SECRET_FIELDS = ("cloud_init",)


def without_secrets(resource):
    """A copy of a resource row with SECRET_FIELDS removed.

    A copy, not the row itself: the row belongs to the caller, and removing a
    field from it in place would change a value the caller still holds.
    """
    return {k: v for k, v in resource.items() if k not in SECRET_FIELDS}


# Every argument these modules share, so one module cannot drift from another
# over what a token is called.
COMMON_ARGS = dict(
    name=dict(type="str", required=True),
    # Required for C(state=absent) as well as C(state=present): the list route
    # that resolves the name refuses without it.
    cluster_id=dict(type="str", required=True),
    state=dict(type="str", choices=["present", "absent"], default="present"),
    wait=dict(type="bool", default=True),
    timeout_seconds=dict(type="int", default=1800),
    poll_interval_seconds=dict(type="int", default=5),
    vvm_api_url=dict(type="str"),
    vvm_api_token=dict(type="str", no_log=True),
    validate_certs=dict(type="bool", default=True),
)


def find_all_by_name(client, collection, cluster_id, name):
    """Every item of the collection whose name matches.

    🚨 C(cluster_id) IS NOT OPTIONAL AND NOT A FILTER. C(GET /v1/vms),
    C(/v1/networks) and C(/v1/datastores) all answer
    C(400 missing required query param: cluster_id) without it. A lookup that
    sent none, and read the 400 as "no such name", would create a second
    resource under a name the control plane already holds and would report
    C(state=absent) as a no-op for a resource that is still there.

    ⛔ AN ERROR IS NOT AN EMPTY LIST EITHER. This raises C(ApiError) so the
    caller fails. Returning C([]) on a failed read is the same defect wearing a
    different hat: a question that could not be asked, answered "nothing".

    Follows C(next_cursor): a walk estate has more networks than one page, and
    a lookup that stopped at the first page would miss a match on page two.
    """
    found = []
    base = collection + "?cluster_id=" + quote(str(cluster_id), safe="")
    path = base
    while path:
        page = client.get(path) or {}
        for item in page.get("items") or []:
            if item.get("name") == name:
                found.append(item)
        cursor = page.get("next_cursor")
        path = (base + "&cursor=" + quote(str(cursor), safe="")) if cursor else None
    return found


def find_by_name(module, client, collection, name):
    """The one item of the collection whose name matches, or None.

    🚨 AMBIGUITY IS REFUSED, NOT RESOLVED. Nothing in the control plane makes a
    name unique — two projects can hold a VM called C(web-01) — and these
    modules address a resource by name. Taking the first match would have
    C(state=absent) delete whichever row the list happened to put first, which
    is a wrong VM deleted on somebody else's project, and C(state=present)
    report C(changed=false) about a row the playbook does not mean.
    """
    try:
        found = find_all_by_name(
            client, collection, module.params["cluster_id"], name,
        )
    except ApiError as err:
        fail_json_from_api_error(module, err)
        return None
    if len(found) > 1:
        module.fail_json(
            msg="%d items in %s are named %r, so this module cannot tell which "
                "one is meant and will not act on any of them. Names are not "
                "unique in the control plane. Ids: %s"
                % (len(found), collection, name, [i.get("id") for i in found]),
            matches=[i.get("id") for i in found],
        )
        return None
    return found[0] if found else None


def _await_job(module, client, job_id, params, what):
    if not job_id or not params["wait"]:
        return None
    try:
        job = client.poll_job(
            job_id,
            timeout_seconds=params["timeout_seconds"],
            interval_seconds=params["poll_interval_seconds"],
        )
    except ApiError as err:
        fail_json_from_api_error(module, err)
        return None
    except TimeoutError as err:
        module.fail_json(msg=str(err))
        return None
    if (job or {}).get("state") != "succeeded":
        module.fail_json(
            msg="the %s job ended in %s, not succeeded" % (what, (job or {}).get("state")),
            job=job,
        )
    return job


def ensure_present(module, client, collection, fqcn, body):
    """Create the resource when the control plane does not hold its name.

    Returns (resource, changed, job). The resource is read back after the job
    settles, because the 202 is a write receipt and not the row.
    """
    params = module.params
    name = params["name"]
    existing = find_by_name(module, client, collection, name)
    if existing:
        return existing, False, None
    if module.check_mode:
        # ⛔ NOT THE BODY. It carries cloud_init, whose user_data holds whatever
        # the playbook put there — a password, a token, a private key — and a
        # module that returned it would print it into the play output and into
        # any log of the run, for a resource that was not even created.
        return {"name": name}, True, None
    key = derive_idempotency_key(fqcn, name, "present", {})
    try:
        receipt = client.post(collection, body, idempotency_key=key) or {}
    except ApiError as err:
        fail_json_from_api_error(module, err)
        return None, False, None
    job = _await_job(module, client, receipt.get("job_id"), params, "create")
    resource = receipt
    if receipt.get("id"):
        try:
            resource = client.get(collection + "/" + receipt["id"]) or receipt
        except ApiError as err:
            fail_json_from_api_error(module, err)
            return None, False, None
    return resource, True, job


def ensure_absent(module, client, collection, fqcn):
    """Delete the resource the control plane holds under this name.

    Returns (resource, changed, job). A name the control plane does not hold is
    C(changed=false) — not a failure, so a playbook can be run twice.
    """
    params = module.params
    name = params["name"]
    existing = find_by_name(module, client, collection, name)
    if not existing:
        return None, False, None
    if module.check_mode:
        return existing, True, None
    if not existing.get("updated_at"):
        module.fail_json(
            msg="%s %s carries no updated_at, so the delete has no If-Match to "
                "send and the control plane would refuse it" % (collection, name),
            resource=existing,
        )
        return None, False, None
    key = derive_idempotency_key(fqcn, existing["id"], "absent", {})
    try:
        receipt = client.delete(
            collection + "/" + existing["id"],
            idempotency_key=key,
            if_match=existing["updated_at"],
        ) or {}
    except ApiError as err:
        fail_json_from_api_error(module, err)
        return None, False, None
    job = _await_job(module, client, receipt.get("job_id"), params, "delete")
    return existing, True, job


def run_resource_module(module, collection, fqcn, result_key, create_fields):
    """Everything the three declarative modules do once their arguments are in.

    🔑 WRITTEN ONCE BECAUSE IT WAS WRITTEN THREE TIMES. Resolving the URL and
    the token, building the client, dispatching on C(state), assembling the
    create body out of the fields the handler decodes, and shaping the result
    were the same twenty-five lines in C(vvm_vm), C(vvm_network) and
    C(vvm_datastore) — so a module that forgot C(validate_certs), or returned
    its resource under a key its RETURN block does not document, would have
    looked exactly like one that did neither.

    C(create_fields) stays with the module, because it is a claim about that
    module's handler: every name in it must be a json tag of that handler's
    request struct, and the maintainers' tests checks it against the Go
    source rather than against this list.
    """
    try:
        api_url = resolve_api_url(module.params.get("vvm_api_url"))
        token = resolve_token(module.params.get("vvm_api_token"))
    except RuntimeError as err:
        module.fail_json(msg=str(err))
        return

    client = ApiClient(api_url, token, validate_certs=module.params["validate_certs"])

    if module.params["state"] == "absent":
        resource, changed, job = ensure_absent(module, client, collection, fqcn)
    else:
        body = {"name": module.params["name"]}
        for field in create_fields:
            if module.params.get(field) is not None:
                body[field] = module.params[field]
        resource, changed, job = ensure_present(
            module, client, collection, fqcn, body,
        )

    result = dict(changed=changed)
    if resource is not None:
        result[result_key] = without_secrets(resource)
    if job is not None:
        result["job"] = job
    module.exit_json(**result)
