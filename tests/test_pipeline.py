import json
from types import SimpleNamespace

import pytest

from bhsync import pipeline
from bhsync import store as st
from bhsync.apply import Conflict, apply_proposal, reject_proposal
from bhsync.match import CHOW, CREATE, DUPLICATE, STALE_CHOW, STALE_REVIEW, UPDATE, build_proposals
from bhsync.normalize import norm_street
from tests.fake_crm import FIX, FakeCRM

BELLHAVEN = "0015QAPLGS3FVYEEEM"
MILLSTONE_SANDUSKY = "0017JP8Z1UQ763BVK3"


def _locations():
    return json.loads((FIX / "locations.json").read_text())["locations"]


def _scrape():
    return SimpleNamespace(locations=_locations(), warnings=[])


@pytest.fixture
def env(tmp_path):
    return st.Store(tmp_path / "state.db"), FakeCRM()


def _run(store, crm):
    return pipeline.run(store=store, crm=crm, scrape_fn=_scrape, snapshot=False)


def _by_account(report):
    return {p.account_id: p for p in report.proposals if p.account_id}


# ---------------- normalisation ----------------
@pytest.mark.parametrize("a,b", [("3313 Wilmington Pike", "3313 Wilmington Pk"),
                                 ("4850 NW Sylvania Ave", "4850 Northwest Sylvania Avenue"),
                                 ("199 Barks Rd W", "199 Barks Road West"),
                                 ("1250 NW Franklin Street", "1250 Northwest Franklin St")])
def test_street_normalisation(a, b):
    assert norm_street(a) == norm_street(b)


# ---------------- matching / classification ----------------
def test_every_case_is_classified_as_expected():
    crm = FakeCRM()
    r = build_proposals(_locations(), crm.list_accounts(), crm.list_contacts())
    by_acct = _by_account(r)
    creates = {p.create_fields["name"] for p in r.proposals if p.kind == CREATE}
    assert creates == {"Amberly Manor", "Bellhaven at Union Square", "Bellhaven of Batavia", "Bellhaven of Carlisle"}
    # SOP: revenue AND AR -> chow; otherwise direct re-parent
    assert by_acct["001A34WFSUYHCRBLFT"].kind == CHOW          # Marietta  51,250 / 3,800
    assert by_acct["001U6RW32TY0WSXZZB"].kind == CHOW          # Tiffin    84,000 / 12,400
    assert by_acct["001LGFPBJY4N9MB6KL"].changes == {"parent_id": BELLHAVEN}   # Lima 47,000 / 0
    assert by_acct["001UKEFGADQ8YCZ4YM"].changes == {"parent_id": BELLHAVEN}   # Findlay (no parent)
    assert by_acct["001SXSF4ELF0Z2LGDM"].kind == STALE_CHOW    # Sandusky -> Millstone
    assert by_acct["001SXSF4ELF0Z2LGDM"].changes == {"chow_current_account": MILLSTONE_SANDUSKY}
    assert {by_acct[i].kind for i in ("00116ETS45BL7DTQP7", "0016PVXH4B25HWR7QE")} == {STALE_REVIEW}
    assert by_acct["001CF3LDWVRGL09P4F"].changes == {"billing_zip": "45662"}  # Portsmouth zip typo
    dups = {p.account_id: p.changes["duplicate_of_account"] for p in r.proposals if p.kind == DUPLICATE}
    assert dups["001QU150PM4Z15UA71"] == "001EGU7BMJ942ZTRE6"   # Owosso: keep exact phone/address match
    assert dups["001B7XZAA3AFALS9GP"] == dups["0016KTS1UAWBRXS09J"] == "001WR41PYNWXCAE2X4"  # Kettering
    # traps: same name in another state, unrelated account in same town
    assert "0015D74ZLRY810RGY5" not in by_acct and "001GNU41AVXZRLLJ9P" not in by_acct
    assert not r.warnings


def test_duplicate_reason_matches_the_ranking_actually_used():
    crm = FakeCRM()
    r = build_proposals(_locations(), crm.list_accounts(), crm.list_contacts())
    why = {p.account_id: next(e for e in p.evidence if e.startswith("Keeping"))
           for p in r.proposals if p.kind == DUPLICATE and p.location_slug}
    # Kettering's survivor is under Harborview (a predecessor), not Bellhaven - the reason must say so
    for loser in ("001B7XZAA3AFALS9GP", "0016KTS1UAWBRXS09J"):
        assert "Harborview" in why[loser] and "already sits under Bellhaven" not in why[loser]
    assert "already sits under Bellhaven" in why["00159PL81N38KM4FHM"]          # Monroe: survivor is under Bellhaven
    assert "stronger match" in why["001QU150PM4Z15UA71"]                       # Owosso: same parent, 1 contact each


# ---------------- end-to-end: approve everything, then re-run ----------------
def test_approve_all_then_rerun_is_clean(env):
    store, crm = env
    first = _run(store, crm)
    assert first["new_proposals"] == len(first["report"].proposals) > 0
    for p in store.list((st.PENDING,)):
        apply_proposal(store, crm, p["id"], reviewer="test")

    a = crm.accounts
    # CHOW: old Marietta untouched except the link; new account under Bellhaven
    old = a["001A34WFSUYHCRBLFT"]
    assert old["parent_id"] == "001FWSQ30SFW6S7604" and old["note"] == "" and old["status"] == "Active"
    new = a[old["chow_current_account"]]
    assert new["parent_id"] == BELLHAVEN and new["name"] == "Bellhaven of Marietta" and new["care_type"] == "Skilled Nursing"
    # Direct re-parent
    assert a["001LGFPBJY4N9MB6KL"]["parent_id"] == BELLHAVEN
    # Sandusky: parent unchanged, linked to Millstone
    assert a["001SXSF4ELF0Z2LGDM"]["parent_id"] == BELLHAVEN
    assert a["001SXSF4ELF0Z2LGDM"]["chow_current_account"] == MILLSTONE_SANDUSKY
    # Duplicates
    assert a["001QU150PM4Z15UA71"]["status"] == "Inactive"
    assert a["001QU150PM4Z15UA71"]["duplicate_of_account"] == "001EGU7BMJ942ZTRE6"

    writes_before = len(crm.writes)
    second = _run(store, crm)
    assert second["new_proposals"] == 0
    assert second["report"].proposals == []          # CRM now agrees with the website
    assert store.list((st.PENDING,)) == []
    assert len(crm.writes) == writes_before          # pipeline itself never writes

    # every website location now maps to exactly one active Bellhaven account
    cov = [c for c in second["report"].coverage if c["location"]]
    assert len(cov) == 35 and all(c["result"].startswith("confident match") for c in cov)


def test_rejected_items_are_not_reproposed(env):
    store, crm = env
    _run(store, crm)
    target = next(p for p in store.list((st.PENDING,)) if p["kind"] == STALE_REVIEW)
    reject_proposal(store, target["id"], reviewer="test", comment="facility confirmed open")
    again = _run(store, crm)
    assert again["skipped_decided"] == 1 and again["new_proposals"] == 0
    assert store.get(target["id"])["status"] == st.REJECTED
    assert len(crm.writes) == 0


def test_double_apply_is_refused(env):
    store, crm = env
    _run(store, crm)
    p = next(p for p in store.list((st.PENDING,)) if p["kind"] == CREATE)
    apply_proposal(store, crm, p["id"], reviewer="test")
    n = len(crm.writes)
    with pytest.raises(Conflict):
        apply_proposal(store, crm, p["id"], reviewer="test")
    assert len(crm.writes) == n


def test_conflict_when_crm_changed_since_proposal(env):
    store, crm = env
    _run(store, crm)
    p = next(p for p in store.list((st.PENDING,)) if p["account_id"] == "001CF3LDWVRGL09P4F")  # Portsmouth zip
    crm.accounts["001CF3LDWVRGL09P4F"]["billing_zip"] = "45663"   # someone edited it meanwhile
    with pytest.raises(Conflict):
        apply_proposal(store, crm, p["id"], reviewer="test")
    assert crm.accounts["001CF3LDWVRGL09P4F"]["billing_zip"] == "45663"
    assert store.get(p["id"])["status"] == st.CONFLICT


def test_sop_rechecked_at_apply_time(env):
    store, crm = env
    _run(store, crm)
    p = next(p for p in store.list((st.PENDING,)) if p["account_id"] == "001LGFPBJY4N9MB6KL")  # Lima direct re-parent
    crm.accounts["001LGFPBJY4N9MB6KL"]["outstanding_ar"] = 500    # AR appeared after the proposal
    with pytest.raises(Conflict):
        apply_proposal(store, crm, p["id"], reviewer="test")
    assert crm.accounts["001LGFPBJY4N9MB6KL"]["parent_id"] != BELLHAVEN


def test_scraper_failure_does_not_flag_everything_stale(env):
    store, crm = env
    _run(store, crm)
    with pytest.raises(RuntimeError):
        pipeline.run(store=store, crm=crm, snapshot=False,
                     scrape_fn=lambda: SimpleNamespace(locations=_locations()[:5], warnings=[]))
    assert all(p["kind"] != STALE_REVIEW or p["account_id"] in ("00116ETS45BL7DTQP7", "0016PVXH4B25HWR7QE")
               for p in store.list((st.PENDING,)))
