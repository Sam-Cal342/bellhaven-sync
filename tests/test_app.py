from types import SimpleNamespace

import pytest

import app as review_app
from bhsync import pipeline
from bhsync import store as st
from tests.fake_crm import FakeCRM
from tests.test_pipeline import _scrape


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = st.Store(tmp_path / "state.db")
    crm = FakeCRM()
    monkeypatch.setattr(review_app, "store", store)
    monkeypatch.setattr(review_app, "_crm", lambda: crm)
    real_run = pipeline.run
    monkeypatch.setattr(review_app.pipeline, "run",
                        lambda store: real_run(store=store, crm=crm, scrape_fn=_scrape, snapshot=False))
    review_app.app.config["TESTING"] = True
    return review_app.app.test_client(), store, crm


def test_pages_render_and_nothing_writes_without_approval(client):
    c, store, crm = client
    assert c.post("/run").status_code == 302
    for path in ("/", "/history", "/coverage", "/audit", "/?kind=chow"):
        r = c.get(path)
        assert r.status_code == 200, path
    page = c.get("/").get_data(as_text=True)
    assert "Billing SOP check" in page and "Approve &amp; write to CRM" in page
    assert crm.writes == []


def test_approve_writes_and_reject_does_not(client):
    c, store, crm = client
    c.post("/run")
    pending = store.list((st.PENDING,))
    chow = next(p for p in pending if p["kind"] == "chow")
    stale = next(p for p in pending if p["kind"] == "stale_review")
    c.post(f"/proposals/{chow['id']}/approve", data={"comment": "SOP applies"})
    assert store.get(chow["id"])["status"] == st.APPROVED
    assert [w[0] for w in crm.writes] == ["POST", "PATCH"]          # create new, link old - nothing else
    assert set(crm.writes[1][2]) == {"chow_current_account"}
    c.post(f"/proposals/{stale['id']}/reject", data={"comment": "known open"})
    assert store.get(stale["id"])["status"] == st.REJECTED and len(crm.writes) == 2
    # re-run: neither comes back
    c.post("/run")
    ids = {p["id"] for p in store.list((st.PENDING,))}
    assert chow["id"] not in ids and stale["id"] not in ids
    # a rejection can be reopened; an approval cannot
    c.post(f"/proposals/{stale['id']}/reopen")
    c.post(f"/proposals/{chow['id']}/reopen")
    assert store.get(stale["id"])["status"] == st.PENDING
    assert store.get(chow["id"])["status"] == st.APPROVED
