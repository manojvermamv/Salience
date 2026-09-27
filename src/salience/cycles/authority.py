"""Current local authority, never a grant of production or delegated authority."""

from hashlib import sha256
import json

from salience.cycles.contracts import AuthoritySnapshot, RunContextV2


def current_authority(connection, workspace_id, subject_id, scope="cycles:write"):
    identity = connection.execute("SELECT issuer,subject,revision,expires_at FROM identity_subjects WHERE id=%s AND workspace_id=%s", (subject_id, workspace_id)).fetchone()
    if not identity or not connection.execute("SELECT * FROM public.p0_lock_identity(%s,%s,%s)", (identity["issuer"], identity["subject"], workspace_id)).fetchone():
        raise PermissionError("current subject authority required")
    identity = connection.execute("SELECT revision,expires_at FROM identity_subjects WHERE id=%s", (subject_id,)).fetchone()
    grant = connection.execute("SELECT id,scope,effect,constraints,expires_at FROM permission_grants WHERE workspace_id=%s AND principal_type='identity' AND principal_id=%s AND scope=%s AND expires_at>clock_timestamp()", (workspace_id, str(subject_id), scope)).fetchone()
    if not grant or grant["effect"] != "allow" or grant["constraints"] != {}:
        raise PermissionError("current scoped authority required")
    payload = {"subject_id": str(subject_id), "workspace_id": str(workspace_id), "scope": scope,
               "identity_revision": identity["revision"], "identity_expires_at": identity["expires_at"].isoformat(),
               "grant_id": str(grant["id"]), "grant_expires_at": grant["expires_at"].isoformat()}
    return payload | {"fingerprint": sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()}


def require_program(connection, workspace_id, program_id):
    program = connection.execute("SELECT * FROM public.v4_lock_program(%s,%s)", (program_id, workspace_id)).fetchone()
    if not program:
        raise PermissionError("active fixture program in current workspace required")


def context_authorized(connection, payload, workspace_id, subject_id):
    if payload.get("schema_version") != "RunContext.local.v2":
        return True
    try:
        context = RunContextV2.model_validate(payload)
        require_program(connection, workspace_id, context.content_program_id)
        authority = current_authority(connection, workspace_id, subject_id)
        now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        return AuthoritySnapshot.model_validate(authority) == context.authority and now < context.execution_deadline
    except PermissionError:
        return False
