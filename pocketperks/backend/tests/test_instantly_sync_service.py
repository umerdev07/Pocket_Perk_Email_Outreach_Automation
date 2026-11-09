import pytest

from app.instantly_sync_service import build_campaign_payload, build_lead_payload


def approved_step(number: int) -> dict:
    return {
        "step_number": number,
        "draft": {"status": "approved"},
    }


def test_campaign_payload_uses_uk_dates_and_keeps_provider_campaign_inactive():
    campaign = {
        "name": "PocketPerks dummy recipient test",
        "start_at": "2026-11-05T00:30:00+00:00",
        "end_at": "2026-11-05T23:00:00+00:00",
        "timezone": "Europe/London",
    }

    payload = build_campaign_payload(campaign, [approved_step(1), approved_step(2)])

    assert payload["campaign_schedule"]["start_date"] == "2026-11-05"
    assert payload["campaign_schedule"]["end_date"] == "2026-11-05"
    assert payload["campaign_schedule"]["schedules"][0]["timezone"] == "Europe/London"
    # A step's saved wait is applied after the previous email in the provider sequence.
    assert payload["sequences"][0]["steps"][0]["delay"] == 3
    assert payload["sequences"][0]["steps"][0]["delay_unit"] == "days"
    assert payload["sequences"][0]["steps"][1]["delay"] == 0
    assert payload["sequences"][0]["steps"][1]["delay_unit"] == "days"
    assert "status" not in payload
    assert "activate" not in payload


def test_campaign_payload_converts_utc_to_bst_date_boundary():
    campaign = {
        "name": "UK summer-time test",
        "start_at": "2026-07-10T23:30:00+00:00",
        "end_at": "2026-07-11T22:30:00+00:00",
        "timezone": "Europe/London",
    }

    payload = build_campaign_payload(campaign, [approved_step(1)])

    # Both UTC timestamps fall on July 11 in Europe/London (BST, UTC+1).
    assert payload["campaign_schedule"]["start_date"] == "2026-07-11"
    assert payload["campaign_schedule"]["end_date"] == "2026-07-11"


def test_campaign_payload_refuses_any_unapproved_step():
    with pytest.raises(ValueError, match="approved generic draft"):
        build_campaign_payload(
            {"name": "Test", "start_date": "2026-11-05", "end_date": "2026-11-05"},
            [{"step_number": 1, "draft": {"status": "generated"}}],
        )


def test_lead_payload_maps_reviewed_personalized_email_variables():
    result = build_lead_payload(
        {"email": "owner@example.co.uk", "business_name": "Example Cafe", "website": "https://example.co.uk"},
        [{"step_number": 1, "status": "queued", "subject": "Hello Example Cafe", "body": "A short intro."}],
    )

    assert result["email"] == "owner@example.co.uk"
    assert result["custom_variables"] == {
        "pp_email_1_subject": "Hello Example Cafe",
        "pp_email_1_body": "A short intro.\n\nBest,\nThe PocketPerks Team\nPocketPerks | VenturesSky Ltd\nhttps://pocketperks.co.uk\n\nIf you would rather not hear from us, just reply 'no thanks'.",
        "pp_business_name": "Example Cafe",
    }
