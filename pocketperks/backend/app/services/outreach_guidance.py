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


def sector_guidance_for(category: object) -> str:
    """Choose only a sector lens supported by the imported category field."""
    normalized = " ".join(str(category or "").casefold().replace("&", "and").split())
    if any(term in normalized for term in ("sandwich", "takeaway sandwich")):
        sector = "sandwich_shop"
    elif any(term in normalized for term in ("barber", "barbershop", "barber shop")):
        sector = "barber"
    elif any(term in normalized for term in ("cafe", "café", "coffee shop", "coffeehouse")):
        sector = "cafe"
    elif any(term in normalized for term in ("restaurant", "bistro", "dining")):
        sector = "restaurant"
    else:
        sector = "general"
    return _SECTOR_GUIDANCE[sector]
