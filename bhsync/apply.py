"""Execute ONE approved proposal against the CRM. Only the review app calls this.

Safety rules:
  * a proposal is claimed atomically (pending -> applying) so it can't be applied twice
  * the account is re-read first; if any field we're about to change was edited since the
    proposal was made, we stop with a 'conflict' instead of overwriting someone's edit
  * the billing SOP is re-checked against LIVE revenue/AR at apply time
  * every write is read back and verified
"""
from __future__ import annotations

from . import config
from . import store as st
from .match import CHOW, CREATE, DUPLICATE, STALE_CHOW, STALE_REVIEW, UPDATE
from .normalize import norm_name, norm_zip


class Conflict(Exception):
    pass


def apply_proposal(store: st.Store, crm, pid: int, reviewer: str, comment: str = "") -> dict:
    if not store.claim(pid):
        current = store.get(pid)
        raise Conflict(f"Proposal {pid} is '{current['status'] if current else 'missing'}', not pending - nothing done")
    row = store.get(pid)
    payload, prior = row["payload"], row["result"] or {}
    log = lambda action, detail: store.log(pid, action, detail)
    try:
        result = _execute(crm, payload, prior, log)
    except Conflict as exc:
        store.set_status(pid, st.CONFLICT, {**prior, "error": str(exc)})
        log("conflict", {"error": str(exc)})
        raise
    except Exception as exc:
        store.set_status(pid, st.FAILED, {**prior, **getattr(exc, "partial", {}), "error": str(exc)})
        log("failed", {"error": str(exc)})
        raise
    store.decide(pid, st.APPROVED, reviewer, comment, result)
    log("approved", {"reviewer": reviewer, "comment": comment, "result": result})
    return result


def reject_proposal(store: st.Store, pid: int, reviewer: str, comment: str = "") -> None:
    row = store.get(pid)
    if not row or row["status"] not in (st.PENDING, st.FAILED, st.CONFLICT):
        raise Conflict(f"Proposal {pid} is not open for review")
    store.decide(pid, st.REJECTED, reviewer, comment)
    store.log(pid, "rejected", {"reviewer": reviewer, "comment": comment})


def reopen_proposal(store: st.Store, pid: int, reviewer: str) -> None:
    """Undo a rejection (approvals can't be undone here - they already changed the CRM)."""
    row = store.get(pid)
    if not row or row["status"] != st.REJECTED:
        raise Conflict(f"Only rejected proposals can be reopened")
    store.set_status(pid, st.PENDING)
    store.log(pid, "reopened", {"reviewer": reviewer, "previous_comment": row.get("reviewer_comment")})


# ------------------------------------------------------------------------------------------
def _check_unchanged(live: dict, before: dict) -> None:
    for k, v in before.items():
        if (live.get(k) or "") != (v or ""):
            raise Conflict(f"'{k}' changed in the CRM since this was proposed (expected {v!r}, now {live.get(k)!r}). "
                           f"Re-run the pipeline to get a fresh proposal.")


def _append_note(existing: str, line: str) -> str:
    if not line or line in (existing or ""):
        return existing or ""
    return f"{existing}\n{line}" if existing else line


def _verify(crm, account_id: str, expected: dict) -> dict:
    live = crm.get_account(account_id)
    bad = {k: (v, live.get(k)) for k, v in expected.items() if (live.get(k) or "") != (v or "")}
    if bad:
        raise RuntimeError(f"Write to {account_id} did not stick: {bad}")
    return {k: live.get(k) for k in expected}


def _patch(crm, account_id: str, body: dict, log) -> dict:
    log("patch", {"account_id": account_id, "body": body})
    resp = crm.update_account(account_id, body)
    log("patch_response", {"account_id": account_id, "response": resp})
    return _verify(crm, account_id, body)


def _create(crm, fields: dict, log) -> str:
    # Guard against double creation (e.g. a retry after a timeout): look for an account we already made.
    existing = [a for a in crm.search_accounts(zip=fields.get("billing_zip", ""))
                if a.get("created_by_candidate") and norm_name(a["name"]) == norm_name(fields["name"])
                and norm_zip(a.get("billing_zip")) == norm_zip(fields.get("billing_zip"))]
    if existing:
        log("create_skipped_existing", {"account_id": existing[0]["account_id"]})
        return existing[0]["account_id"]
    log("create", {"body": fields})
    resp = crm.create_account(fields)
    log("create_response", {"response": resp})
    new_id = None
    if isinstance(resp, dict):
        new_id = resp.get("account_id") or resp.get("id") or (resp.get("data") or {}).get("account_id")
    if not new_id:  # fall back to finding it
        found = [a for a in crm.search_accounts(zip=fields.get("billing_zip", ""))
                 if a.get("created_by_candidate") and a["name"] == fields["name"]]
        if not found:
            raise RuntimeError(f"Create returned no id and the new account could not be found: {resp!r}")
        new_id = found[0]["account_id"]
    _verify(crm, new_id, {k: v for k, v in fields.items() if k != "note"})
    return new_id


def _execute(crm, p: dict, prior: dict, log) -> dict:
    kind = p["kind"]

    if kind == CREATE:
        new_id = _create(crm, p["create_fields"], log)
        return {"created_account_id": new_id}

    live = crm.get_account(p["account_id"])
    _check_unchanged(live, p.get("before") or {})
    rev, ar = live.get("lifetime_revenue") or 0, live.get("outstanding_ar") or 0

    if kind == CHOW:
        if not config.requires_chow(rev, ar):
            raise Conflict(f"SOP outcome changed: live revenue {rev}, AR {ar} no longer requires a CHOW. "
                           "Re-run the pipeline; it will propose a direct re-parent instead.")
        new_id = prior.get("created_account_id")
        if not new_id:
            new_id = _create(crm, p["create_fields"], log)
        try:
            # SOP: the old account is left exactly as is - the ONLY field touched is the CHOW link.
            after = _patch(crm, p["account_id"], {"chow_current_account": new_id}, log)
        except Exception as exc:
            exc.partial = {"created_account_id": new_id}  # retry will reuse the new account
            raise
        return {"created_account_id": new_id, "old_account_id": p["account_id"], "old_account_after": after}

    if kind == STALE_CHOW:
        if not config.requires_chow(rev, ar):
            raise Conflict(f"SOP outcome changed: live revenue {rev}, AR {ar}. Re-run the pipeline.")
        return {"after": _patch(crm, p["account_id"], dict(p["changes"]), log)}

    if kind in (UPDATE, DUPLICATE, STALE_REVIEW):
        changes = dict(p["changes"])
        if "parent_id" in changes and config.requires_chow(rev, ar):
            raise Conflict(f"SOP: account now has revenue {rev} and AR {ar}, so it must not be re-parented. "
                           "Re-run the pipeline; it will propose a change of ownership instead.")
        body = dict(changes)
        if p.get("note_line"):
            body["note"] = _append_note(live.get("note") or "", p["note_line"])
        return {"after": _patch(crm, p["account_id"], body, log)}

    raise ValueError(f"Unknown proposal kind {kind}")
