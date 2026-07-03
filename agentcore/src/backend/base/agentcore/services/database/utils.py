from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from alembic.util.exc import CommandError
from loguru import logger
from sqlmodel import select, text
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from agentcore.services.database.service import DatabaseService


async def initialize_database(*, fix_migration: bool = False) -> None:
    logger.debug("Initializing database")
    from agentcore.services.deps import get_db_service

    database_service: DatabaseService = get_db_service()
    try:
        if database_service.settings_service.settings.database_connection_retry:
            await database_service.create_db_and_tables_with_retry()
        else:
            await database_service.create_db_and_tables()
    except Exception as exc:
        # if the exception involves tables already existing
        # we can ignore it
        if "already exists" not in str(exc):
            msg = "Error creating DB and tables"
            logger.exception(msg)
            raise RuntimeError(msg) from exc
    try:
        await database_service.check_schema_health()
    except Exception as exc:
        msg = "Error checking schema health"
        logger.exception(msg)
        raise RuntimeError(msg) from exc
    try:
        await database_service.run_migrations(fix=fix_migration)
    except CommandError as exc:
        # if "overlaps with other requested revisions" or "Can't locate revision identified by"
        # are not in the exception, we can't handle it
        if "overlaps with other requested revisions" not in str(
            exc
        ) and "Can't locate revision identified by" not in str(exc):
            raise
        # This means there's wrong revision in the DB
        # We need to delete the alembic_version table
        # and run the migrations again
        logger.warning("Wrong revision in DB, deleting alembic_version table and running migrations again")
        async with session_getter(database_service) as session:
            await session.exec(text("DROP TABLE alembic_version"))
        await database_service.run_migrations(fix=fix_migration)
    except Exception as exc:
        # if the exception involves tables already existing
        # we can ignore it
        if "already exists" not in str(exc):
            logger.exception(exc)
        raise
    try:
        await _seed_predefined_tags(database_service)
    except Exception:
        logger.exception("Failed to seed predefined tags (non-fatal)")
    try:
        await _seed_help_support_faq(database_service)
    except Exception:
        logger.exception("Failed to seed Help & Support FAQ (non-fatal)")
    try:
        from agentcore.services.idp.catalogue import seed_idp_templates
        async with session_getter(database_service) as session:
            await seed_idp_templates(session)
    except Exception:
        logger.exception("Failed to seed IDP templates (non-fatal)")
    logger.debug("Database initialized")


async def _seed_predefined_tags(db_service: DatabaseService) -> None:
    """Insert predefined tags if they don't already exist."""
    from agentcore.services.database.models.tag.model import PREDEFINED_TAGS, Tag

    async with session_getter(db_service) as session:
        for tag_def in PREDEFINED_TAGS:
            existing = (
                await session.exec(
                    select(Tag).where(Tag.name == tag_def["name"], Tag.is_predefined.is_(True))
                )
            ).first()
            if not existing:
                session.add(
                    Tag(
                        name=tag_def["name"],
                        category=tag_def["category"],
                        description=tag_def["description"],
                        is_predefined=True,
                        org_id=None,
                        created_by=None,
                    )
                )
        await session.commit()
    logger.debug("Predefined tags seeded")


async def _seed_help_support_faq(db_service: DatabaseService) -> None:
    """Insert default Help & Support FAQ questions if they don't already exist."""
    from agentcore.services.database.models.help_support.model import HelpSupportQuestion

    question_text = "How do I configure Rules / Conditions in the IDP Flow Canvas?"
    answer_text = (
        "You can define custom routing rules using the 'Rules / Conditions' node in the IDP Flow Canvas. "
        "These rules determine whether a processed document is automatically approved or sent to pending review.\n\n"
        "1. Anatomy of a Condition:\n"
        "Each condition in the JSON array expects the following structure:\n"
        "  - field: The name of the header field or table column to check (e.g., 'invoice_value', 'supplier_name', 'amount'). Use 'confidence' to check the overall document extraction confidence.\n"
        "  - op: The operator defining the check type.\n"
        "  - value: The target comparison value.\n\n"
        "2. Supported Operators:\n"
        "  - Numeric: 'gt' (>), 'lt' (<), 'gte' (>=), 'lte' (<=), 'eq' (==), 'neq' (!=)\n"
        "  - Text: 'eq' (exact match), 'neq' (does not match), 'contains', 'starts_with', 'ends_with'\n"
        "  - Date: 'gt', 'lt', 'gte', 'lte', 'eq', 'neq' (parsed dynamically)\n"
        "  - Presence: 'is_present', 'is_missing' (checks if a field was extracted or not)\n"
        "  - Visual Elements: Check 'signature', 'checkbox', 'qr', 'barcode' presence or checkbox state using 'is_present' or '==' (e.g. 'checked').\n\n"
        "3. Logic Operator (AND vs. OR):\n"
        "  - AND: All condition rows must evaluate to True. If even one fails, the document goes to Pending Review.\n"
        "  - OR: If at least one condition evaluates to True, the document is approved. If all fail, it goes to Pending Review.\n\n"
        "4. Hands-On JSON Examples:\n"
        "  * Example 1: High Confidence & Amount Threshold (AND)\n"
        "    Auto-approve only if the overall confidence is 85%+ and the amount is positive:\n"
        "    [{\"field\":\"confidence\",\"op\":\"gte\",\"value\":0.85}, {\"field\":\"amount\",\"op\":\"gt\",\"value\":0}]\n\n"
        "  * Example 2: Required PO Presence for Auto-Approval (AND)\n"
        "    Ensure that a Purchase Order (PO) number was successfully extracted, and the invoice has a positive total:\n"
        "    [{\"field\":\"po_number\",\"op\":\"is_present\"}, {\"field\":\"invoice_value\",\"op\":\"gt\",\"value\":0}]\n\n"
        "  * Example 3: Filter by Currency or Amount Limit (AND)\n"
        "    Approve documents under $5,000 USD only:\n"
        "    [{\"field\":\"currency\",\"op\":\"eq\",\"value\":\"USD\"}, {\"field\":\"invoice_value\",\"op\":\"lt\",\"value\":5000}]\n\n"
        "  * Example 4: Visual Signature Check (OR)\n"
        "    Approve if a signature is detected on the page OR if the checkbox is explicitly checked:\n"
        "    [{\"field\":\"signature\",\"op\":\"is_present\"}, {\"field\":\"checkbox\",\"op\":\"eq\",\"value\":\"checked\"}]"
    )

    async with session_getter(db_service) as session:
        existing = (
            await session.exec(
                select(HelpSupportQuestion).where(HelpSupportQuestion.question == question_text)
            )
        ).first()
        if not existing:
            session.add(HelpSupportQuestion(question=question_text, answer=answer_text))
            await session.commit()
            logger.debug("Help & Support rules FAQ seeded successfully")


@asynccontextmanager
async def session_getter(db_service: DatabaseService):
    try:
        session = AsyncSession(db_service.engine, expire_on_commit=False)
        yield session
    except Exception:
        logger.exception("Session rollback because of exception")
        await session.rollback()
        raise
    finally:
        await session.close()


@dataclass
class Result:
    name: str
    type: str
    success: bool


@dataclass
class TableResults:
    table_name: str
    results: list[Result]
