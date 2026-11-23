"""Match website locations to CRM accounts and turn every discrepancy into a reviewable proposal.

Nothing in this module writes to the CRM. It is a pure function of (locations, accounts, contacts).
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date

from . import config
from .normalize import (is_po_box, name_similarity, norm_city, norm_phone, norm_street, norm_zip,
                        same_address, street_number)

# Proposal kinds
UPDATE = "update"            # fix fields on the matched account (incl. direct re-parent)
CHOW = "chow"                # SOP: create new account under Bellhaven, link old one via chow_current_account
CREATE = "create"            # location on the website with no CRM account
DUPLICATE = "duplicate"      # extra account for the same facility -> Inactive + duplicate_of_account
STALE_REVIEW = "stale_review"  # under Bellhaven in CRM, not on the website, no evidence of a buyer
STALE_CHOW = "stale_chow"    # not on website, another owner's account exists, billing must be preserved

KIND_LABELS = {
    UPDATE: "Fix existing account", CHOW: "Change of ownership (SOP)", CREATE: "New account",
    DUPLICATE: "Mark duplicate", STALE_REVIEW: "Not on website", STALE_CHOW: "Sold, billing preserved (SOP)",
}


@dataclass
class Candidate:
    account: dict
    score: int
    signals: list[str]
    name_sim: float


@dataclass
class Proposal:
    kind: str
    title: str
    summary: str
    account_id: str | None = None
    location_slug: str | None = None
    changes: dict = field(default_factory=dict)        # PATCH body for account_id
    before: dict = field(default_factory=dict)         # values those fields must still have at apply time
    create_fields: dict | None = None                  # POST body (create / chow)
    note_line: str = ""                                # appended to the account note when applied
    evidence: list[str] = field(default_factory=list)
    sop: dict | None = None
    confidence: str = "high"
    score: int | None = None
    accounts: list[dict] = field(default_factory=list)  # snapshots of every account involved, for display
    website: dict | None = None
    display: list[dict] = field(default_factory=list)   # human-readable before/after rows for the review app
    fingerprint: str = ""

    def finalize(self) -> "Proposal":
        """Fingerprint = what is being proposed, not when. Notes/evidence are excluded so a re-run
        that proposes the same change produces the same fingerprint and is recognised as decided."""
        key = {
            "kind": self.kind, "account_id": self.account_id, "location": self.location_slug,
            "changes": {k: v for k, v in self.changes.items() if k != "note"},
            "create": {k: v for k, v in (self.create_fields or {}).items() if k != "note"},
        }
        self.fingerprint = hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]
        return self

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MatchReport:
    proposals: list[Proposal]
    coverage: list[dict]          # one row per website location + one per stale CRM account
    warnings: list[str]
    target_parent_id: str


# --------------------------------------------------------------------------------------------
# Context helpers
# --------------------------------------------------------------------------------------------
def is_parent_account(a: dict) -> bool:
    return "(parent account)" in (a.get("name") or "").lower()


def is_retired(a: dict) -> bool:
    """Accounts already resolved by convention: losing duplicates and CHOW'd originals."""
    return bool(a.get("duplicate_of_account") or a.get("chow_current_account"))


def resolve_parents(accounts: list[dict]) -> tuple[str, dict[str, int]]:
    tops = [a for a in accounts if not a.get("parent_id") and is_parent_account(a)]
    target = [a for a in tops if a["name"].lower().startswith(config.TARGET_PARENT_NAME.lower())]
    if len(target) != 1:
        raise RuntimeError(f"Expected exactly one '{config.TARGET_PARENT_NAME}' parent account, found {len(target)}")
    rank = {target[0]["account_id"]: config.TARGET_PARENT_RANK}
    for a in tops:
        for name, r in config.PREDECESSOR_PARENT_RANK.items():
            if a["name"].lower().startswith(name.lower()):
                rank[a["account_id"]] = r
    return target[0]["account_id"], rank


def _loc(l) -> dict:
    return l if isinstance(l, dict) else asdict(l)


def desired_care_type(current: str, offerings: list[str]) -> tuple[str, str | None]:
    mapped = [config.CARE_OFFERING_MAP.get(o.strip().lower()) for o in offerings]
    mapped = [m for m in mapped if m]
    unknown = [o for o in offerings if config.CARE_OFFERING_MAP.get(o.strip().lower()) is None]
    note = f"Unrecognised care offering(s) on website: {', '.join(unknown)}" if unknown else None
    if not mapped or current in mapped:
        return current, note
    return mapped[0], note


def _acct_view(a: dict, role: str, contacts: list[dict]) -> dict:
    keep = ("account_id", "name", "parent_id", "parent_name", "billing_street", "billing_city", "billing_state",
            "billing_zip", "care_type", "status", "phone", "lifetime_revenue", "outstanding_ar",
            "chow_current_account", "duplicate_of_account", "note")
    v = {k: a.get(k) for k in keep}
    v["role"] = role
    v["contacts"] = [f"{c['name']} ({c.get('title') or 'no title'}{', ' + c['email'] if c.get('email') else ''})"
                     for c in contacts]
    return v


def _addr(a: dict) -> str:
    return f"{a.get('billing_street')}, {a.get('billing_city')}, {a.get('billing_state')} {a.get('billing_zip')}"


def _site_addr(l: dict) -> str:
    return f"{l['street']}, {l['city']}, {l['state']} {l['zip']}"


def _money(x) -> str:
    return f"${(x or 0):,.0f}"


# --------------------------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------------------------
def _strip_brand(name: str | None) -> str:
    """Every Bellhaven name shares the brand word, which inflates similarity between different facilities."""
    words = [w for w in (name or "").split() if w.lower().strip(",.") not in config.BRAND_WORDS]
    return " ".join(words) or (name or "")


def score_pair(loc: dict, a: dict) -> Candidate:
    sim = name_similarity(_strip_brand(loc["name"]), _strip_brand(a.get("name")))
    if (a.get("billing_state") or "").upper() != loc["state"].upper():
        return Candidate(a, 0, [f"Different state ({a.get('billing_state') or 'blank'} vs {loc['state']})"], sim)
    score, signals = 0, []
    same_zip = norm_zip(a.get("billing_zip")) == norm_zip(loc["zip"])
    same_city = norm_city(a.get("billing_city")) == norm_city(loc["city"])
    street_eq = bool(norm_street(loc["street"])) and norm_street(loc["street"]) == norm_street(a.get("billing_street"))
    num_eq = street_number(loc["street"]) is not None and street_number(loc["street"]) == street_number(a.get("billing_street"))

    if street_eq and same_zip:
        score += 60
        same_text = a.get("billing_street") == loc["street"]
        signals.append("Street and ZIP match exactly" if same_text else
                       f"Street and ZIP match after normalising ('{a.get('billing_street')}' = '{loc['street']}')")
    elif street_eq and same_city:
        score += 45
        signals.append(f"Street matches in the same city but ZIP differs (CRM {a.get('billing_zip')}, website {loc['zip']})")
    elif num_eq and same_zip:
        score += 35
        signals.append(f"Street number and ZIP match, street name differs ('{a.get('billing_street')}')")
    elif is_po_box(a.get("billing_street")) and same_zip:
        score += 10
        signals.append(f"CRM address is a PO Box ('{a.get('billing_street')}') in the same ZIP")

    if sim >= 0.9:
        score += 30
        signals.append(f"Name matches (similarity {sim:.2f})")
    elif sim >= 0.75:
        score += 15
        signals.append(f"Name similar (similarity {sim:.2f})")
    if norm_phone(a.get("phone")) and norm_phone(a.get("phone")) == norm_phone(loc.get("phone")):
        score += 20
        signals.append("Phone number matches")
    if same_city and score:
        score += 5
        signals.append("Same city")
    return Candidate(a, score, signals, sim)


def _survivor_key(c: Candidate, rank: dict[str, int], contacts_by: dict[str, list]):
    a = c.account
    return ((a.get("lifetime_revenue") or 0) > 0, rank.get(a.get("parent_id"), 0),
            len(contacts_by.get(a["account_id"], [])), c.score, a.get("status") == "Active")


def _survivor_reason(surv: Candidate, loser: Candidate, rank: dict[str, int], contacts_by: dict[str, list],
                     target_id: str) -> str:
    """Explain the first element of _survivor_key that separates the two, in the same order."""
    a, la = surv.account, loser.account
    if (a.get("lifetime_revenue") or 0) > 0 and not (la.get("lifetime_revenue") or 0) > 0:
        return "survivor has billing history"
    ra, rl = rank.get(a.get("parent_id"), 0), rank.get(la.get("parent_id"), 0)
    if ra > rl:
        if a.get("parent_id") == target_id:
            return "survivor already sits under Bellhaven"
        loser_parent = f"this account's parent ({la['parent_name']})" if la.get("parent_name") else "an account with no parent"
        return (f"survivor's parent, {a.get('parent_name')}, is a company Bellhaven acquired communities from "
                f"and ranks above {loser_parent}")
    if ra == rl:
        na, nl = len(contacts_by.get(a["account_id"], [])), len(contacts_by.get(la["account_id"], []))
        if na > nl:
            return "survivor has more contacts"
        if na == nl and surv.score > loser.score:
            return "survivor is the stronger match to the website"
    return "survivor is the active account" if a.get("status") == "Active" else "survivor ranked higher on the tie-breakers"


# --------------------------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------------------------
def build_proposals(locations, accounts: list[dict], contacts: list[dict], today: date | None = None) -> MatchReport:
    today = today or date.today()
    stamp = f"[{today.isoformat()} Bellhaven sync]"
    locs = [_loc(l) for l in locations]
    target_id, rank = resolve_parents(accounts)
    by_id = {a["account_id"]: a for a in accounts}
    target_name = by_id[target_id]["name"]
    contacts_by: dict[str, list] = defaultdict(list)
    for c in contacts:
        if c.get("is_active", True):
            contacts_by[c["account_id"]].append(c)

    eligible = [a for a in accounts if not is_parent_account(a) and not is_retired(a)]
    warnings: list[str] = []
    proposals: list[Proposal] = []
    coverage: list[dict] = []

    # 1) Score every (location, account) pair; each account goes to its single best location.
    cands: dict[str, list[Candidate]] = {l["slug"]: [] for l in locs}
    assigned: dict[str, list[Candidate]] = defaultdict(list)
    for a in eligible:
        best_slug, best = None, None
        for l in locs:
            c = score_pair(l, a)
            cands[l["slug"]].append(c)
            if c.score >= config.MATCH_THRESHOLD and (best is None or c.score > best.score):
                best_slug, best = l["slug"], c
        if best_slug:
            assigned[best_slug].append(best)
    matched_ids = {c.account["account_id"] for cs in assigned.values() for c in cs}

    # 2) One decision per website location
    for l in locs:
        site_ev = f"Listed on Bellhaven's website: {l['url']} ({_site_addr(l)}; {', '.join(l['care_offerings']) or 'no care offerings'})"
        group = sorted(assigned.get(l["slug"], []), key=lambda c: _survivor_key(c, rank, contacts_by), reverse=True)

        if not group:
            near = [c for c in cands[l["slug"]] if c.account["account_id"] not in matched_ids and
                    (c.name_sim >= config.NEAR_MISS_NAME_RATIO or
                     (norm_city(c.account.get("billing_city")) == norm_city(l["city"]) and
                      (c.account.get("billing_state") or "").upper() == l["state"].upper()))]
            near = sorted(near, key=lambda c: (c.name_sim, c.score), reverse=True)[:3]
            care, care_note = desired_care_type("", l["care_offerings"])
            fields = {"name": l["name"], "parent_id": target_id, "billing_street": l["street"],
                      "billing_city": l["city"], "billing_state": l["state"], "billing_zip": l["zip"],
                      "status": "Active", "phone": l.get("phone", ""),
                      "note": f"{stamp} Created from Bellhaven website listing {l['url']}"}
            if care:
                fields["care_type"] = care
            ev = [site_ev, "No CRM account matches this address, or this name + phone, in this state"]
            for c in near:
                why = "; ".join(c.signals) or "no address/phone overlap"
                ev.append(f"Considered and rejected: {c.account['name']} ({_addr(c.account)}, parent "
                          f"{c.account.get('parent_name') or 'none'}) - score {c.score}, name similarity {c.name_sim:.2f}; {why}")
            if care_note:
                ev.append(care_note)
            proposals.append(Proposal(
                kind=CREATE, title=f"New account: {l['name']}",
                summary=f"Create {l['name']} ({l['city']}, {l['state']}) under {target_name}",
                location_slug=l["slug"], create_fields=fields, evidence=ev, website=l,
                confidence="high" if not any(c.score >= 30 for c in near) else "medium",
                accounts=[_acct_view(c.account, "rejected candidate", contacts_by.get(c.account["account_id"], [])) for c in near],
            ).finalize())
            coverage.append({"location": l["name"], "slug": l["slug"], "result": "new - no CRM account", "account_id": None})
            continue

        surv = group[0]
        a = surv.account
        aid = a["account_id"]
        acct_contacts = contacts_by.get(aid, [])
        ev = [site_ev] + [f"Match signal: {s}" for s in surv.signals] + [f"Match score {surv.score}"]
        bh_emails = [c["email"] for c in acct_contacts if c.get("email", "").lower().endswith("@bellhavenliving.com")]
        if bh_emails:
            ev.append(f"Account has Bellhaven contact(s): {', '.join(bh_emails)}")
        confidence = "high" if surv.score >= config.HIGH_CONFIDENCE else "medium"

        # 2a) Extra accounts for the same facility
        for loser in group[1:]:
            la = loser.account
            reason = _survivor_reason(surv, loser, rank, contacts_by, target_id)
            dev = [site_ev, f"Both accounts match this location: {', '.join(loser.signals)}",
                   f"Keeping {a['name']} ({aid}) because {reason}",
                   f"This account: revenue {_money(la.get('lifetime_revenue'))}, AR {_money(la.get('outstanding_ar'))}, "
                   f"{len(contacts_by.get(la['account_id'], []))} contact(s), parent {la.get('parent_name') or 'none'}"]
            if (la.get("lifetime_revenue") or 0) > 0:
                dev.append("WARNING: this duplicate has billing history - billing should confirm before deactivation")
            loser_contacts = contacts_by.get(la["account_id"], [])
            if loser_contacts:
                dev.append(f"Note: {len(loser_contacts)} contact(s) stay attached to this inactive account "
                           f"({', '.join(c['name'] for c in loser_contacts)}) - re-home manually if still current")
            ch = {"duplicate_of_account": aid, "status": "Inactive"}
            proposals.append(Proposal(
                kind=DUPLICATE, title=(f"Duplicate: {la['name']} -> keep {a['name']}" if la["name"] != a["name"] else
                                       f"Duplicate: {la['name']} ({la['account_id'][-6:]}) -> keep {aid[-6:]}"),
                summary=f"Mark {la['name']} ({la.get('parent_name') or 'no parent'}) Inactive as a duplicate of {a['name']}",
                account_id=la["account_id"], location_slug=l["slug"], changes=ch,
                before={k: la.get(k) for k in ch},
                note_line=f"{stamp} Duplicate of {a['name']} ({aid}) - same facility at {_site_addr(l)}.",
                evidence=dev, website=l, score=loser.score,
                confidence="high" if loser.score >= config.HIGH_CONFIDENCE else "medium",
                accounts=[_acct_view(la, "duplicate (will be inactivated)", contacts_by.get(la["account_id"], [])),
                          _acct_view(a, "survivor", acct_contacts)],
            ).finalize())

        # 2b) Field-level fixes on the survivor
        changes: dict = {}
        if (a.get("name") or "").strip() != l["name"]:
            changes["name"] = l["name"]
        if norm_street(a.get("billing_street")) != norm_street(l["street"]):
            changes["billing_street"] = l["street"]
        if norm_city(a.get("billing_city")) != norm_city(l["city"]):
            changes["billing_city"] = l["city"]
        if (a.get("billing_state") or "").upper() != l["state"].upper():
            changes["billing_state"] = l["state"]
        if norm_zip(a.get("billing_zip")) != norm_zip(l["zip"]):
            changes["billing_zip"] = l["zip"]
        care, care_note = desired_care_type(a.get("care_type") or "", l["care_offerings"])
        if care != (a.get("care_type") or ""):
            changes["care_type"] = care
        if care_note:
            ev.append(care_note)
        if a.get("status") != "Active":
            changes["status"] = "Active"
        wrong_parent = a.get("parent_id") != target_id

        if wrong_parent:
            rev, ar = a.get("lifetime_revenue") or 0, a.get("outstanding_ar") or 0
            chow = config.requires_chow(rev, ar)
            sop = {"lifetime_revenue": rev, "outstanding_ar": ar, "chow_required": chow,
                   "from_parent": a.get("parent_name") or "(no parent)", "to_parent": target_name,
                   "rule": ("Revenue history AND outstanding AR > 0 -> preserve old account, create new one, link via chow_current_account"
                            if chow else "No revenue history or no outstanding AR -> re-parent the existing account directly")}
            if chow:
                care_new, _ = desired_care_type("", l["care_offerings"])
                fields = {"name": l["name"], "parent_id": target_id, "billing_street": l["street"],
                          "billing_city": l["city"], "billing_state": l["state"], "billing_zip": l["zip"],
                          "status": "Active", "phone": l.get("phone", ""),
                          "note": (f"{stamp} Change of ownership: replaces {a['name']} ({aid}) formerly under "
                                   f"{a.get('parent_name') or 'no parent'}, which is preserved for billing "
                                   f"(revenue {_money(rev)}, AR {_money(ar)}).")}
                if care_new or a.get("care_type"):
                    fields["care_type"] = care_new or a.get("care_type")
                proposals.append(Proposal(
                    kind=CHOW, title=f"Change of ownership: {l['name']}",
                    summary=(f"{a.get('parent_name') or 'No parent'} -> {target_name}. Old account has revenue "
                             f"{_money(rev)} and AR {_money(ar)}, so it stays untouched; create a new account and link it."),
                    account_id=aid, location_slug=l["slug"],
                    changes={"chow_current_account": "<new account id>"},
                    before={"parent_id": a.get("parent_id"), "chow_current_account": a.get("chow_current_account") or ""},
                    create_fields=fields, evidence=ev, sop=sop, website=l, score=surv.score, confidence=confidence,
                    accounts=[_acct_view(a, "old account (preserved, gets chow link)", acct_contacts)],
                ).finalize())
                coverage.append({"location": l["name"], "slug": l["slug"], "result": "change of ownership (SOP)", "account_id": aid})
                continue
            changes["parent_id"] = target_id
        else:
            sop = None

        if not changes:
            coverage.append({"location": l["name"], "slug": l["slug"], "account_id": aid,
                             "result": "confident match - no change" + (f" (+{len(group) - 1} duplicate)" if len(group) > 1 else "")})
            continue

        labels = {"name": "rename", "billing_street": "address", "billing_city": "address", "billing_state": "address",
                  "billing_zip": "address", "care_type": "care type", "status": "reactivate", "parent_id": "re-parent"}
        what = sorted({labels[k] for k in changes}, key=list(labels.values()).index)
        note_bits = []
        if "parent_id" in changes:
            note_bits.append(f"Re-parented from {a.get('parent_name') or 'no parent'} to {target_name}")
        if "name" in changes:
            note_bits.append(f"renamed from '{a.get('name')}'")
        if any(k.startswith("billing_") for k in changes):
            note_bits.append(f"address was '{_addr(a)}'")
        proposals.append(Proposal(
            kind=UPDATE, title=f"Fix {a['name']}: {', '.join(what)}",
            summary=f"Update {a['name']} to match the Bellhaven website listing ({', '.join(what)})",
            account_id=aid, location_slug=l["slug"], changes=changes, before={k: a.get(k) for k in changes},
            note_line=f"{stamp} {'; '.join(note_bits) or 'Updated'} per {l['url']}.",
            evidence=ev, sop=sop, website=l, score=surv.score, confidence=confidence,
            accounts=[_acct_view(a, "matched account", acct_contacts)],
        ).finalize())
        coverage.append({"location": l["name"], "slug": l["slug"], "account_id": aid,
                         "result": "needs fix: " + ", ".join(what) + (f" (+{len(group) - 1} duplicate)" if len(group) > 1 else "")})

    # 3) CRM accounts that claim to be Bellhaven but are not on the website
    in_scope = [a for a in eligible if a["account_id"] not in matched_ids and
                (a.get("parent_id") == target_id or "bellhaven" in (a.get("name") or "").lower())]
    for a in in_scope:
        aid = a["account_id"]
        if a.get("status") != "Active":
            coverage.append({"location": None, "slug": None, "account_id": aid,
                             "result": f"not on website - already {a.get('status')}", "crm_name": a["name"]})
            continue
        rev, ar = a.get("lifetime_revenue") or 0, a.get("outstanding_ar") or 0
        others = [o for o in eligible if o["account_id"] != aid and o.get("parent_id") != target_id and
                  same_address(a.get("billing_street"), a.get("billing_city"), a.get("billing_state"), a.get("billing_zip"),
                               o.get("billing_street"), o.get("billing_city"), o.get("billing_state"), o.get("billing_zip"))]
        others.sort(key=lambda o: (bool(o.get("parent_id")), o.get("status") == "Active"), reverse=True)
        ev = [f"Not listed anywhere on Bellhaven's website (home page + all directory pages checked)",
              f"CRM: {_addr(a)}, parent {a.get('parent_name') or 'none'}, revenue {_money(rev)}, AR {_money(ar)}"]
        view = [_acct_view(a, "CRM account not on website", contacts_by.get(aid, []))]
        if others:
            o = others[0]
            ev.append(f"Another account exists at the same address under a different owner: {o['name']} ({o['account_id']}), "
                      f"parent {o.get('parent_name') or 'none'} - facility appears to have changed hands")
            view.append(_acct_view(o, "new owner's account", contacts_by.get(o["account_id"], [])))
            chow = config.requires_chow(rev, ar)
            sop = {"lifetime_revenue": rev, "outstanding_ar": ar, "chow_required": chow,
                   "from_parent": a.get("parent_name") or "(no parent)", "to_parent": o.get("parent_name") or "(no parent)",
                   "rule": ("Revenue history AND outstanding AR > 0 -> keep this account exactly as is; link it to the new "
                            "owner's existing account via chow_current_account (no new account needed)" if chow else
                            "No outstanding billing -> treat as a duplicate of the new owner's account")}
            if chow:
                ch = {"chow_current_account": o["account_id"]}
                proposals.append(Proposal(
                    kind=STALE_CHOW, title=f"Sold: {a['name']} -> {o.get('parent_name') or o['name']}",
                    summary=(f"Facility now belongs to {o.get('parent_name') or 'another owner'}. Billing history "
                             f"({_money(rev)} revenue, {_money(ar)} AR) means the old account stays as is; link it to {o['name']}."),
                    account_id=aid, changes=ch, before={"chow_current_account": a.get("chow_current_account") or "",
                                                         "parent_id": a.get("parent_id")},
                    evidence=ev, sop=sop, accounts=view, confidence="medium").finalize())
                coverage.append({"location": None, "slug": None, "account_id": aid, "crm_name": a["name"],
                                 "result": "not on website - sold (CHOW link, billing preserved)"})
            else:
                ch = {"duplicate_of_account": o["account_id"], "status": "Inactive"}
                proposals.append(Proposal(
                    kind=DUPLICATE, title=f"Sold/duplicate: {a['name']} -> keep {o['name']}",
                    summary=f"Facility now under {o.get('parent_name') or 'another owner'}; mark this Bellhaven copy Inactive as a duplicate",
                    account_id=aid, changes=ch, before={k: a.get(k) for k in ch},
                    note_line=f"{stamp} Not on Bellhaven website; same facility as {o['name']} ({o['account_id']}).",
                    evidence=ev, sop=sop, accounts=view, confidence="medium").finalize())
                coverage.append({"location": None, "slug": None, "account_id": aid, "crm_name": a["name"],
                                 "result": "not on website - duplicate of new owner's account"})
            continue

        if rev > 0:
            ev.append("Has billing history - status change only; parent and billing fields untouched")
        ch = {"status": "Needs Review"}
        proposals.append(Proposal(
            kind=STALE_REVIEW, title=f"Not on website: {a['name']}",
            summary=f"{a['name']} is under {a.get('parent_name') or 'no parent'} in the CRM but not on Bellhaven's website. Flag for review rather than deactivate.",
            account_id=aid, changes=ch, before={"status": a.get("status")},
            note_line=f"{stamp} Not listed on Bellhaven website; possibly closed, sold or renamed - verify before deactivating.",
            evidence=ev + ["No other CRM account at this address, so no evidence of a buyer; absence from a marketing site is not proof of closure"],
            accounts=view, confidence="medium").finalize())
        coverage.append({"location": None, "slug": None, "account_id": aid, "crm_name": a["name"],
                         "result": "not on website - flag Needs Review"})

    # Human-readable before/after rows (ids -> names) for the reviewer
    labels = {"name": "Name", "parent_id": "Parent account", "status": "Status", "care_type": "Care type",
              "billing_street": "Street", "billing_city": "City", "billing_state": "State", "billing_zip": "ZIP",
              "phone": "Phone", "chow_current_account": "CHOW current account", "duplicate_of_account": "Duplicate of",
              "note": "Note"}
    ref = lambda i: f"{by_id[i]['name']} ({i})" if i in by_id else (i or "(blank)")
    for p in proposals:
        cur = by_id.get(p.account_id, {})
        for k, v in p.changes.items():
            if k == "parent_id":
                before = cur.get("parent_name") or "(none)"
            elif k in ("chow_current_account", "duplicate_of_account"):
                before = ref(cur.get(k)) if cur.get(k) else "(blank)"
            else:
                before = cur.get(k)
            after = (ref(v) if k in ("parent_id", "chow_current_account", "duplicate_of_account") and v in by_id
                     else "id of the new account created on approval" if v == "<new account id>" else v)
            p.display.append({"field": labels.get(k, k), "before": before if before not in (None, "") else "(blank)",
                              "after": after, "target": "existing account"})
        if p.note_line:
            p.display.append({"field": "Note (appended)", "before": cur.get("note") or "(blank)", "after": p.note_line,
                              "target": "existing account"})
        for k, v in (p.create_fields or {}).items():
            p.display.append({"field": labels.get(k, k), "before": "", "after": ref(v) if k == "parent_id" else v,
                              "target": "new account"})

    # Sanity: one proposal per account (a second one would make apply order matter)
    seen: dict[str, str] = {}
    for p in proposals:
        if p.account_id:
            if p.account_id in seen:
                warnings.append(f"Account {p.account_id} has two proposals ({seen[p.account_id]}, {p.kind})")
            seen[p.account_id] = p.kind
    return MatchReport(proposals=proposals, coverage=coverage, warnings=warnings, target_parent_id=target_id)
