import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.config import Settings
from app.services.risk_service import _rule_assessment, assess_personalized_email


def settings():
    return Settings(_env_file=None, claude_api_key="test-key", claude_model="claude-sonnet-5-5")


def facts():
    return ({"subject": "Hello", "body": "Would you be open to a short conversation?"},
            {"business_name": "Cafe Example", "email": "hello@example.test", "city": "Leeds"})


@pytest.mark.parametrize("subject,body,lead,expected", [
    ("", "Body", {"business_name": "Cafe", "email": "c@example.test"}, "HIGH"),
    ("Hello [Business]", "Body", {"business_name": "Cafe", "email": "c@example.test"}, "HIGH"),
    ("Hello", "Body", {"business_name": "", "email": "c@example.test"}, "LOW"),
    ("Guaranteed results", "A short email.", {"business_name": "Cafe", "email": "c@example.test"}, "MEDIUM"),
    ("URGENT: You’ve won", "Verify your account now.", {"business_name": "Cafe", "email": "c@example.test"}, "HIGH"),
    ("Hello", "Visit https://new-offer.example.com", {"business_name": "Cafe", "email": "c@example.test"}, "LOW"),
    ("Hello", "We have 25 locations.", {"business_name": "Cafe", "email": "c@example.test"}, "LOW"),
    ("A subject longer than eighty characters " + "x" * 50, "Body",
     {"business_name": "Cafe", "email": "c@example.test"}, "MEDIUM"),
    ("Hello", "Word " * 201, {"business_name": "Cafe", "email": "c@example.test"}, "MEDIUM"),
    ("HELLO THERE EVERYONE!", "Body!!!", {"business_name": "Cafe", "email": "c@example.test"}, "MEDIUM"),
])
def test_rule_checks_assign_expected_risk_level(subject, body, lead, expected):
    draft, _ = facts()
    level, reasons = _rule_assessment(subject, body, draft, lead)
    assert level == expected
    assert bool(reasons) is (expected != "LOW")


def test_more_severe_rule_level_is_not_overridden_by_ai_low():
    draft, lead = facts()
    with patch("app.services.risk_service.chat_completion_json", new=AsyncMock(return_value={
        "level": "LOW", "reason": "No AI concerns found."})) as ai:
        result = asyncio.run(assess_personalized_email("Guaranteed", "Act now!", draft=draft, lead=lead,
                                                       settings=settings()))
    assert result["risk_level"] == "MEDIUM"
    assert "associated with spam" in result["risk_reason"]
    ai.assert_awaited_once()


def test_ai_high_is_preserved_only_for_a_strong_spam_pattern():
    draft, lead = facts()
    with patch("app.services.risk_service.chat_completion_json", new=AsyncMock(return_value={
        "level": "HIGH", "reason": "The copy uses a strong misleading urgency pattern."})):
        result = asyncio.run(assess_personalized_email("Hello", "Would you like to talk?", draft=draft,
                                                       lead=lead, settings=settings()))
    assert result == {"risk_level": "HIGH", "risk_reason": "AI spam-risk check: The copy uses a strong misleading urgency pattern."}


def test_ai_failure_uses_lightweight_rules_instead_of_flagging_every_email():
    draft, lead = facts()
    with patch("app.services.risk_service.chat_completion_json", new=AsyncMock(side_effect=RuntimeError("offline"))):
        result = asyncio.run(assess_personalized_email("Hello", "Would you be open to a short conversation?",
                                                       draft=draft, lead=lead, settings=settings()))
    assert result["risk_level"] == "LOW"
    assert "AI check unavailable" in result["risk_reason"]
    assert "Gmail placement cannot be predicted" in result["risk_reason"]


def test_ai_only_receives_email_content_and_does_not_flag_context_mismatch():
    draft, lead = facts()
    campaign = {"instructions": "Introduce the loyalty stamp to Leeds cafes."}
    step = {"context": "Ask whether they would consider a brief conversation."}
    with patch("app.services.risk_service.chat_completion_json", new=AsyncMock(return_value={
        "level": "LOW", "reason": "No obvious spam-like content."})) as ai:
        result = asyncio.run(assess_personalized_email(
            "Hello", "Would you like more information?", draft=draft, lead=lead, settings=settings(),
            campaign=campaign, step=step))
    submitted = ai.await_args.args[0][1]["content"]
    assert "subject" in submitted and "body" in submitted
    assert campaign["instructions"] not in submitted
    assert step["context"] not in submitted
    assert result["risk_level"] == "LOW"


def test_city_and_campaign_fit_are_outside_spam_risk_assessment():
    draft, lead = facts()
    campaign = {"instructions": "Introduce the Loyalty Stamp to independent restaurants in York."}
    with patch("app.services.risk_service.chat_completion_json", new=AsyncMock(return_value={
        "level": "LOW", "reason": "No material concern; selected lead is in the imported audience."})) as ai:
        result = asyncio.run(assess_personalized_email("Hello Cafe Example", "Would you be open to a chat?",
                                                       draft=draft, lead=lead, settings=settings(), campaign=campaign))
    prompt = ai.await_args.args[0][0]["content"]
    assert "Do not judge campaign fit" in prompt
    assert result["risk_level"] == "LOW"


def test_multiple_strong_spam_signals_get_specific_explanation():
    draft, lead = facts()
    level, reasons = _rule_assessment(
        "URGENT: You’ve won", "Verify your account now.", draft, lead)
    assert level == "HIGH"
    assert reasons
    assert any("deceptive urgency" in reason for reason in reasons)
    assert any("unrealistic promise or prize" in reason for reason in reasons)
