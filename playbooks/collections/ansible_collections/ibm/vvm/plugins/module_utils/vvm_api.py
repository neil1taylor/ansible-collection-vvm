# -*- coding: utf-8 -*-
# Copyright (c) 2026 VVM platform team — MIT
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import json
import os
import re
import time
from configparser import ConfigParser

from ansible.module_utils.urls import Request, ConnectionError, SSLValidationError
from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError
from ansible.module_utils.six.moves.urllib.parse import quote, urlencode

JOB_STATE_PENDING = "pending"
JOB_STATE_RUNNING = "running"
JOB_STATE_SUCCEEDED = "succeeded"
JOB_STATE_FAILED = "failed"
TERMINAL_STATES = (JOB_STATE_SUCCEEDED, JOB_STATE_FAILED)

IAM_TOKEN_URL = "https://iam.cloud.ibm.com/identity/token"

# Pattern for VVM resource ids that may appear in URL paths or HTTP headers.
# Bounded to URL-safe characters so an operator-supplied id (often a Jinja
# expansion like {{ inventory_hostname }}) cannot smuggle slashes, query
# delimiters, or CRLF.
_VALID_RESOURCE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def validate_resource_id(value, kind="resource_id"):
    """Validate a VVM resource id intended for URL-path concatenation.

    Allows only [A-Za-z0-9_-]{1,64}. Raises ValueError on mismatch so callers
    can surface a clean module.fail_json. Use this for every module argument
    that ends up in a URL path or as an Idempotency-Key prefix.
    """
    if not isinstance(value, str) or not _VALID_RESOURCE_ID.fullmatch(value):
        raise ValueError(
            "%s must match [A-Za-z0-9_-]{1,64}; got %r" % (kind, value)
        )
    return value


def quote_path_segment(value):
    """Percent-encode a single path segment so /, ?, # never leak through.

    Defense-in-depth alongside validate_resource_id — even if a future caller
    skips validation, the URL stays well-formed.
    """
    return quote(value, safe="")


def resolve_token(module_arg_token):
    """arg -> VVM_API_TOKEN env -> ~/.vvm/credentials -> IBMCLOUD_API_KEY exchange."""
    if module_arg_token:
        return module_arg_token
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
    raise RuntimeError(
        "No VVM API credentials. Set VVM_API_TOKEN, write ~/.vvm/credentials, "
        "or pass vvm_api_token to the module."
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


def resolve_api_url(module_arg_url):
    if module_arg_url:
        return module_arg_url.rstrip("/")
    env_url = os.environ.get("VVM_API_URL")
    if env_url:
        return env_url.rstrip("/")
    raise RuntimeError(
        "No VVM API URL. Set VVM_API_URL or pass vvm_api_url to the module."
    )


class ApiError(Exception):
    """Raised on non-2xx. Carries the structured lifecycle API envelope."""

    def __init__(self, status, message, envelope=None):
        super(ApiError, self).__init__(message)
        self.status = status
        self.envelope = envelope or {}
        self.code = self.envelope.get("code") or self.envelope.get("error")
        details = self.envelope.get("details", {}) or {}
        self.fields = details.get("fields", {}) or {}
        self.blockers = details.get("blockers", []) or []


class ApiClient(object):
    def __init__(self, base_url, token, validate_certs=True, timeout=30,
                 correlation_id=None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.validate_certs = validate_certs
        self.timeout = timeout
        self.correlation_id = correlation_id

    def _headers(self, extra=None):
        h = {
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json",
            "User-Agent": "ibm.vvm-ansible/0.1.0",
        }
        if self.correlation_id:
            h["X-VVM-Correlation-ID"] = self.correlation_id
        if extra:
            h.update(extra)
        return h

    def _send(self, method, path, body=None, extra_headers=None):
        url = self.base_url + path
        req = Request(timeout=self.timeout, validate_certs=self.validate_certs)
        data = None
        headers = self._headers(extra_headers)
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        try:
            resp = req.open(method=method, url=url, data=data, headers=headers)
        except HTTPError as e:
            raw = e.read() if hasattr(e, "read") else b""
            envelope = None
            try:
                envelope = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                envelope = None
            msg = (
                (envelope or {}).get("message")
                or (envelope or {}).get("error")
                or "HTTP %d" % e.code
            )
            raise ApiError(e.code, msg, envelope)
        except (URLError, ConnectionError, SSLValidationError) as e:
            raise RuntimeError("VVM API connection error: %s" % str(e))
        if resp.status == 204:
            return None
        raw = resp.read()
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def get(self, path):
        return self._send("GET", path)

    def post(self, path, body, idempotency_key):
        return self._send(
            "POST", path, body=body,
            extra_headers={"Idempotency-Key": idempotency_key},
        )

    def delete(self, path, idempotency_key, if_match=None):
        """DELETE, with the If-Match every lifecycle route requires.

        the control plane rejects a PUT or DELETE with no If-Match header
        (428) and the handlers then require it to be the row's updated_at as
        RFC3339Nano, so a caller that omits it never deletes anything.
        """
        headers = {"Idempotency-Key": idempotency_key}
        if if_match:
            headers["If-Match"] = if_match
        return self._send("DELETE", path, extra_headers=headers)

    def get_job(self, job_id):
        return self.get("/v1/jobs/" + job_id)

    def poll_job(self, job_id, timeout_seconds=1800, interval_seconds=2,
                 sleep_fn=time.sleep, now_fn=time.monotonic):
        """Poll GET /v1/jobs/{id} until terminal or timeout. Returns the
        terminal job dict; raises TimeoutError on overflow. sleep_fn + now_fn
        are injectable for tests.
        """
        deadline = now_fn() + timeout_seconds
        while True:
            job = self.get_job(job_id)
            state = job.get("state")
            if state in TERMINAL_STATES:
                return job
            if now_fn() >= deadline:
                raise TimeoutError(
                    "Job %s did not reach terminal state within %d seconds "
                    "(last state: %s)" % (job_id, timeout_seconds, state)
                )
            sleep_fn(interval_seconds)
