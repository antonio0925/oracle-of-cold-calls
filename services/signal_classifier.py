"""
Signal classifier — maps raw signal types to action tiers.

Tier 1 (HOT): Immediate action required — demo requests, pricing page hits, etc.
Tier 2 (WARM): Enrich then decide — paywall hits, feature exploration, etc.
Tier 3 (AMBIENT): Park for later — general product usage, content downloads, etc.
"""

# Signal type -> tier mapping
# Loaded fresh each call so you can edit this file without restarting
SIGNAL_TIERS = {
    # Tier 1: HOT — queue immediately for punch list
    "demo_request": 1,
    "pricing_page": 1,
    "contact_sales": 1,
    "free_trial_signup": 1,
    "hand_raise": 1,
    "inbound_call": 1,
    "reply_positive": 1,
    "meeting_booked": 1,

    # Tier 2: WARM — enrich then decide
    "paywall_hit": 2,
    "feature_exploration": 2,
    "return_visit": 2,
    "content_download": 2,
    "webinar_attended": 2,
    "email_opened_multiple": 2,
    "competitor_comparison": 2,

    # Tier 3: AMBIENT — park for batch review
    "product_usage": 3,
    "blog_visit": 3,
    "social_engagement": 3,
    "newsletter_open": 3,
    "generic_pageview": 3,
}

# Tier metadata
TIER_CONFIG = {
    1: {
        "label": "HOT",
        "action": "queued_hot",
        "description": "Immediate action — added to punch list",
        "color": "#FF4500",
    },
    2: {
        "label": "WARM",
        "action": "enriching",
        "description": "Enriching contact before routing",
        "color": "#FFD700",
    },
    3: {
        "label": "AMBIENT",
        "action": "parked",
        "description": "Parked for batch review",
        "color": "#4682B4",
    },
}


def classify_signal(signal_type):
    """Classify a signal type into a tier.

    Returns (tier_number, tier_config) or (None, None) for unknown signals.
    """
    tier = SIGNAL_TIERS.get(signal_type)
    if tier is None:
        return None, None
    return tier, TIER_CONFIG[tier]
