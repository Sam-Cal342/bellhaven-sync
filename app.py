"""Review app. Run:  python app.py   then open http://127.0.0.1:5000

Nothing is written to the CRM except when a reviewer clicks Approve on a single proposal.
"""
from __future__ import annotations

import os
import secrets

from flask import Flask, flash, redirect, render_template, request, url_for

from bhsync import config, pipeline
from bhsync import store as st
from bhsync.apply import Conflict, apply_proposal, reject_proposal, reopen_proposal
from bhsync.crm import CRMClient
from bhsync.match import CHOW, CREATE, DUPLICATE, KIND_LABELS, STALE_CHOW, STALE_REVIEW, UPDATE

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", secrets.token_hex(16))
store = st.Store()
REVIEWER = os.environ.get("REVIEWER", "reviewer")
KIND_ORDER = [CHOW, STALE_CHOW, UPDATE, DUPLICATE, CREATE, STALE_REVIEW]


def _crm():
    return CRMClient()


@app.context_processor
def helpers():
    return {"KIND_LABELS": KIND_LABELS, "crm_link": lambda aid: f"{config.CRM_BROWSER_URL}/{aid}" if aid else None,
            "counts": store.counts(), "last_run": store.last_run(), "reviewer": REVIEWER}


@app.get("/")
def queue():
    kind = request.args.get("kind")
    items = store.list((st.PENDING, st.FAILED, st.CONFLICT))
    by_kind = {k: sum(1 for i in items if i["kind"] == k) for k in KIND_ORDER}
    if kind:
        items = [i for i in items if i["kind"] == kind]
    items.sort(key=lambda i: (KIND_ORDER.index(i["kind"]), i["title"]))
    return render_template("queue.html", items=items, by_kind=by_kind, kind=kind, KIND_ORDER=KIND_ORDER)


@app.get("/history")
def history():
    items = store.list((st.APPROVED, st.REJECTED, st.SUPERSEDED))
    items.sort(key=lambda i: i["decided_at"] or "", reverse=True)
    return render_template("history.html", items=items)


@app.get("/coverage")
def coverage():
    return render_template("coverage.html")


@app.get("/audit")
def audit():
    return render_template("audit.html", rows=store.audit())


@app.post("/proposals/<int:pid>/approve")
def approve(pid: int):
    comment = request.form.get("comment", "").strip()
    try:
        result = apply_proposal(store, _crm(), pid, reviewer=REVIEWER, comment=comment)
    except Conflict as exc:
        flash(f"Not applied - {exc}", "error")
    except Exception as exc:  # network / API error: proposal stays retryable as 'failed'
        flash(f"CRM write failed, nothing marked approved: {exc}", "error")
    else:
        p = store.get(pid)
        target = result.get("created_account_id") or p["account_id"]
        flash({"text": f"Approved and written to CRM: {p['title']}", "link": f"{config.CRM_BROWSER_URL}/{target}"}, "ok")
    return redirect(request.referrer or url_for("queue"))


@app.post("/proposals/<int:pid>/reject")
def reject(pid: int):
    try:
        reject_proposal(store, pid, reviewer=REVIEWER, comment=request.form.get("comment", "").strip())
        flash(f"Rejected: {store.get(pid)['title']} (will not be proposed again)", "info")
    except Conflict as exc:
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("queue"))


@app.post("/proposals/<int:pid>/reopen")
def reopen(pid: int):
    try:
        reopen_proposal(store, pid, reviewer=REVIEWER)
        flash(f"Reopened: {store.get(pid)['title']} is back in the review queue", "info")
    except Conflict as exc:
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("history"))


@app.post("/run")
def run_pipeline():
    try:
        out = pipeline.run(store=store)
        flash(f"Pipeline run {out['run_id']}: {out['locations']} locations, {out['accounts']} accounts -> "
              f"{out['new_proposals']} new, {out['refreshed']} still pending, "
              f"{out['skipped_decided']} already decided (skipped), {out['superseded']} superseded", "info")
    except Exception as exc:
        flash(f"Pipeline failed: {exc}", "error")
    return redirect(url_for("queue"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)
