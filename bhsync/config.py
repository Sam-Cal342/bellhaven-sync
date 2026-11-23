"""Central configuration. Business rules that a reviewer might want to tweak live here."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so the token never has to be hard-coded or committed."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "https://analyst-assessment-production.up.railway.app").rstrip("/")
CRM_BASE_URL = os.environ.get("CRM_BASE_URL", f"{SITE_BASE_URL}/api/v1").rstrip("/")
CRM_API_TOKEN = os.environ.get("CRM_API_TOKEN", "")
# Read-only CRM browser, used only to build "view in CRM" links in the review app.
CRM_BROWSER_URL = os.environ.get("CRM_BROWSER_URL", f"{SITE_BASE_URL}/crm/{CRM_API_TOKEN}/accounts")

DB_PATH = Path(os.environ.get("BHSYNC_DB", ROOT / "data" / "state.db"))
SNAPSHOT_DIR = Path(os.environ.get("BHSYNC_SNAPSHOTS", ROOT / "data" / "snapshots"))

# ---- Parent accounts (resolved by name at runtime, so this works on any CRM copy) ----
TARGET_PARENT_NAME = "Bellhaven Senior Living"
# Companies Bellhaven acquired communities from (per bellhaven /about page).
# Higher rank = preferred survivor when the same facility exists more than once.
# Harborview was acquired outright (2025); only "select" Cedar Trail communities moved (2026).
PREDECESSOR_PARENT_RANK = {
    "Harborview Care Group": 2,
    "Cedar Trail Communities": 1,
}
TARGET_PARENT_RANK = 3

# ---- Field vocabularies (the API does NOT validate care_type, so we must) ----
VALID_STATUSES = {"Active", "Inactive", "Needs Review"}
VALID_CARE_TYPES = {"Skilled Nursing", "Assisted Living", "Memory Care", "Independent Living"}
# Website "care offering" label -> CRM care_type
CARE_OFFERING_MAP = {
    "short-term rehabilitation & nursing": "Skilled Nursing",
    "skilled nursing": "Skilled Nursing",
    "assisted living": "Assisted Living",
    "memory support": "Memory Care",
    "memory care": "Memory Care",
    "independent living": "Independent Living",
}

# ---- Matching thresholds (points; see match.score_pair) ----
MATCH_THRESHOLD = 50       # below this a CRM account is not considered the same facility
HIGH_CONFIDENCE = 80       # at or above this the match is labelled "high confidence"
NEAR_MISS_NAME_RATIO = 0.7  # rejected candidates above this name similarity are shown as evidence
BRAND_WORDS = {"bellhaven"}  # ignored when comparing names (shared by every community)

# ---- SOP: change of ownership ----
def requires_chow(lifetime_revenue: float, outstanding_ar: float) -> bool:
    """Billing SOP: revenue history AND outstanding AR > 0 means the old account must be preserved."""
    return (lifetime_revenue or 0) > 0 and (outstanding_ar or 0) > 0
