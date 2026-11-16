"""Approved PocketPerks facts and Harrogate-kit sector guidance for AI prompts."""

APPROVED_PRODUCT_FACTS = (
    "PocketPerks provides a digital loyalty card customers keep on their phone; "
    "it is an alternative to paper loyalty cards. The core loyalty card has no "
    "monthly subscription. Paid email, SMS, or app-message marketing is optional "
    "and must never be described as part of the free core card. Setup can include "
    "agreeing a simple reward and preparing personalised QR launch materials."
)

_SECTOR_GUIDANCE = {
    "sandwich_shop": (
        "For a sandwich shop, focus on making a familiar loyalty card easier to "
        "keep on a phone and on recognising regular lunch customers. Do not promise "
        "more weekday trade or suggest a specific reward unless the campaign brief "
        "has approved it."
    ),
    "cafe": (
        "For a café, focus on a phone-based alternative to paper stamp cards and "
        "rewarding regular customers. Do not mention reviews, review journeys, "
        "activity reporting, or pilot results unless a client has separately confirmed them."
    ),
    "barber": (
        "For a barber, focus on a simple digital loyalty card for regular clients. "
        "Do not assume visit frequency, make birthday or win-back messaging claims, "
        "or promise that a client will return by a particular date."
    ),
    "restaurant": (
        "For a restaurant, describe a phone-based alternative to paper loyalty cards "
        "for its customers. Do not assume the restaurant's service style, visit frequency, "
        "or reward, and do not promise a particular business outcome."
    ),
    "general": (
        "Use the general digital-loyalty-card description. Do not force a café, "
        "sandwich-shop, or barber-specific problem onto another business."
    ),
}


def _sector_from_text(value: object) -> str | None:
    normalized = " ".join(str(value or "").casefold().replace("&", "and").split())
    found = set()
    if any(term in normalized for term in ("sandwich", "takeaway sandwich")):
        found.add("sandwich_shop")
    if any(term in normalized for term in ("barber", "barbershop", "barber shop")):
        found.add("barber")
    if any(term in normalized for term in ("cafe", "café", "coffee shop", "coffeehouse")):
        found.add("cafe")
    if any(term in normalized for term in ("restaurant", "bistro", "dining")):
        found.add("restaurant")
    return next(iter(found)) if len(found) == 1 else None


def verified_sector_for_lead(category: object, business_name: object = "") -> str | None:
    """Prefer an explicit sector in the business name over a conflicting import label."""
    category_sector = _sector_from_text(category)
    name_sector = _sector_from_text(business_name)
    if name_sector:
        return name_sector
    return category_sector or name_sector


def category_for_copy(category: object, business_name: object = "") -> object:
    """Use a clear business-name sector when the imported category contradicts it."""
    name_sector = _sector_from_text(business_name)
    category_sector = _sector_from_text(category)
    if name_sector and category_sector and name_sector != category_sector:
        return name_sector
    return category


def sector_guidance_for(category: object, business_name: object = "") -> str:
    """Choose a sector lens only when the lead data is internally consistent."""
    sector = verified_sector_for_lead(category, business_name)
    return _SECTOR_GUIDANCE.get(sector or "general", _SECTOR_GUIDANCE["general"])
