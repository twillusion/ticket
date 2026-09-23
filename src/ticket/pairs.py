"""Can we buy exactly 2 seats together from this listing?"""

from __future__ import annotations

import json

YES = "yes"                    # the listing's split options explicitly include 2
FILTER_ONLY = "filter-only"    # no split info; relies on the site's quantity=2 filter
NO = "no"


def pair_status(quantity: int | None, allowed_splits_json: str | None, requested_quantity: int | None) -> str:
    if quantity is not None and quantity < 2:
        return NO
    if allowed_splits_json is not None:
        return YES if 2 in json.loads(allowed_splits_json) else NO
    if requested_quantity == 2:
        return FILTER_ONLY
    return NO
