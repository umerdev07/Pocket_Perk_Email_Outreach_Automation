import json

from app.context_validation import campaign_instructions_issue, context_alignment_issue, context_issue
from app.services.outreach_guidance import category_for_copy, verified_sector_for_lead
from app.services.personalization_service import _personalization_messages


def test_random_context_is_rejected_but_relevant_context_is_accepted():
    assert context_issue("sdnakj")
    assert context_issue("Introduce PocketPerks digital loyalty card and invite demo") is None


def test_campaign_instructions_need_a_relevant_topic_and_clear_action():
    assert campaign_instructions_issue("sdnakj")
    assert campaign_instructions_issue("Café loyalty stamps")
    assert campaign_instructions_issue(
        "Introduce the PocketPerks loyalty card to café owners and invite them to a demo."
    ) is None


def test_generated_copy_must_reflect_context_topics():
    context = "Mention no monthly subscription and invite a demo"
    assert context_alignment_issue(
        context, "A digital card", "PocketPerks helps businesses connect with customers."
    )
    assert context_alignment_issue(
        context,
        "A loyalty card with no monthly subscription",
        "Would you be open to a short demo?",
    ) is None


def test_explicit_business_name_resolves_conflicting_imported_category():
    assert verified_sector_for_lead("Barber Shop", "El's Cafe") == "cafe"
    assert category_for_copy("Barber Shop", "El's Cafe") == "cafe"
    messages = _personalization_messages(
        {"name": "Campaign", "instructions": "Introduce a loyalty card"},
        {"context": "Introduce the digital loyalty card", "draft": {"subject": "Hello", "body": "A loyalty card"}},
        {"business_name": "El's Cafe", "business_category": "Barber Shop"},
    )
    data = json.loads(messages[1]["content"])
    assert data["lead_data"]["business_category"] == "cafe"
    assert "For a café" in data["sector_guidance_for_verified_category"]
