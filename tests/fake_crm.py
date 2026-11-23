"""In-memory stand-in for the CRM API, loaded from the real snapshot. Mirrors the server rules we observed."""
from __future__ import annotations

import copy
import itertools
import json
from pathlib import Path

from bhsync.crm import CRMError, _validate

FIX = Path(__file__).parent / "fixtures"
MUTABLE = {"name", "parent_id", "status", "note", "care_type", "phone", "billing_street", "billing_city",
           "billing_state", "billing_zip", "chow_current_account", "duplicate_of_account"}


class FakeCRM:
    def __init__(self):
        self.accounts = {a["account_id"]: a for a in json.loads((FIX / "accounts.json").read_text())["data"]}
        self.contacts = json.loads((FIX / "contacts.json").read_text())["data"]
        self._ids = itertools.count(1)
        self.writes: list[tuple] = []

    def list_accounts(self):
        return copy.deepcopy(list(self.accounts.values()))

    def list_contacts(self):
        return copy.deepcopy(self.contacts)

    def get_account(self, account_id):
        if account_id not in self.accounts:
            raise CRMError("not found", 404)
        return copy.deepcopy(self.accounts[account_id])

    def search_accounts(self, **params):
        z = params.get("zip")
        return [copy.deepcopy(a) for a in self.accounts.values() if not z or a["billing_zip"] == z]

    def _apply(self, acct, fields):
        if "parent_id" in fields:
            if fields["parent_id"] and fields["parent_id"] not in self.accounts:
                raise CRMError("parent_id does not exist", 400)
            acct["parent_name"] = self.accounts[fields["parent_id"]]["name"] if fields["parent_id"] else ""
        acct.update({k: v for k, v in fields.items() if k in MUTABLE})

    def create_account(self, fields):
        _validate(fields)
        if not fields.get("name"):
            raise CRMError("name is required", 400)
        aid = f"001FAKE{next(self._ids):011d}"
        acct = {"account_id": aid, "name": "", "parent_id": "", "parent_name": "", "billing_street": "",
                "billing_city": "", "billing_state": "", "billing_zip": "", "care_type": "", "status": "Active",
                "phone": "", "lifetime_revenue": 0, "outstanding_ar": 0, "chow_current_account": "",
                "duplicate_of_account": "", "note": "", "created_by_candidate": True}
        self._apply(acct, fields)
        self.accounts[aid] = acct
        self.writes.append(("POST", aid, dict(fields)))
        return {"account_id": aid, "message": "created"}

    def update_account(self, account_id, fields):
        _validate(fields)
        self._apply(self.accounts[account_id], fields)
        self.writes.append(("PATCH", account_id, dict(fields)))
        return {"account_id": account_id, "message": "updated", "fields": list(fields)}
