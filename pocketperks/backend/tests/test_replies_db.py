import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app import replies_db
from app.config import Settings


settings = Settings(_env_file=None)


def test_reply_list_uses_postgresql_repository():
    pool, rows = object(), [{"id": "reply-1", "body": "Hello"}]
    with patch("app.replies_postgres.list_replies", new=AsyncMock(return_value=rows)) as listing:
        result = asyncio.run(replies_db.list_replies(settings, limit=20, offset=10, pool=pool))
    assert result == rows
    listing.assert_awaited_once_with(pool, settings, limit=20, offset=10)


def test_reply_database_operations_fail_closed_without_postgres():
    with pytest.raises(HTTPException) as raised:
        asyncio.run(replies_db.list_replies(settings))
    assert raised.value.status_code == 503


def test_empty_provider_reply_page_is_a_no_op_without_database_access():
    assert asyncio.run(replies_db.save_received_replies(settings, [], pool=None)) == 0


@pytest.mark.parametrize("body,expected", [
    ("Please unsubscribe me", True),
    ("No thanks", True),
    ("Thanks, I will review your unsubscribe policy", False),
    ("Hi\n> Please reply no thanks to unsubscribe", False),
])
def test_explicit_opt_out_classifier_ignores_quoted_campaign_text(body, expected):
    assert replies_db._explicit_opt_out(body) is expected
