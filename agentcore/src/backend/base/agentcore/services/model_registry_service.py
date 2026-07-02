"""CRUD operations for the model registry."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agentcore.services.database.models.model_registry.model import (
    ModelRegistry,
    ModelRegistryCreate,
    ModelRegistryRead,
    ModelRegistryUpdate,
)
from agentcore.utils.crypto import decrypt_api_key, decrypt_api_key_with_fallback, derive_fernet_key, encrypt_api_key

logger = logging.getLogger(__name__)


def _encryption_key() -> str:
    """Return the primary encryption key from environment."""
    key = os.getenv("MODEL_REGISTRY_ENCRYPTION_KEY", "")
    if not key:
        raw = os.getenv("WEBUI_SECRET_KEY", "default-agentcore-registry-key")
        key = derive_fernet_key(raw)
    return key


async def create_model(
    session: AsyncSession,
    data: ModelRegistryCreate,
) -> ModelRegistryRead:
    """Insert a new model into the registry."""
    enc_key = _encryption_key()
    environments = [str(v).lower() for v in (getattr(data, "environments", None) or []) if v]
    if not environments:
        environments = [str(data.environment).lower()]
    row = ModelRegistry(
        display_name=data.display_name,
        description=data.description,
        provider=data.provider,
        model_name=data.model_name,
        model_type=data.model_type,
        base_url=data.base_url,
        environment=data.environment,
        environments=environments,
        source_model_id=getattr(data, "source_model_id", None),
        org_id=getattr(data, "org_id", None),
        dept_id=getattr(data, "dept_id", None),
        public_dept_ids=[str(v) for v in (getattr(data, "public_dept_ids", None) or [])] or None,
        created_by_id=getattr(data, "created_by_id", None),
        visibility_scope=getattr(data, "visibility_scope", "private"),
        approval_status=getattr(data, "approval_status", "approved"),
        requested_by=getattr(data, "requested_by", None),
        request_to=getattr(data, "request_to", None),
        requested_at=getattr(data, "requested_at", None),
        reviewed_at=getattr(data, "reviewed_at", None),
        reviewed_by=getattr(data, "reviewed_by", None),
        review_comments=getattr(data, "review_comments", None),
        review_attachments=getattr(data, "review_attachments", None),
        provider_config=data.provider_config,
        capabilities=data.capabilities,
        default_params=data.default_params,
        is_active=data.is_active,
        cost_limit=data.cost_limit,
        current_cost=0.0,
        created_by=data.created_by,
    )

    if data.api_key and enc_key:
        row.api_key_secret_ref = encrypt_api_key(data.api_key, enc_key)

    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ModelRegistryRead.from_orm_model(row)


async def get_models(
    session: AsyncSession,
    *,
    provider: str | None = None,
    environment: str | None = None,
    model_type: str | None = None,
    active_only: bool = True,
) -> list[ModelRegistryRead]:
    """Return all registry entries, optionally filtered."""
    stmt = select(ModelRegistry)
    if active_only:
        stmt = stmt.where(ModelRegistry.is_active.is_(True))
    if provider:
        stmt = stmt.where(ModelRegistry.provider == provider)
    if model_type:
        stmt = stmt.where(ModelRegistry.model_type == model_type)
    stmt = stmt.order_by(ModelRegistry.provider, ModelRegistry.display_name)

    result = await session.execute(stmt)
    rows = result.scalars().all()
    if environment:
        env_lower = str(environment).lower()
        filtered = []
        for row in rows:
            row_envs = [str(v).lower() for v in (getattr(row, "environments", None) or []) if v]
            if row_envs:
                if env_lower not in row_envs:
                    continue
            elif str(getattr(row, "environment", "") or "").lower() != env_lower:
                continue
            filtered.append(row)
        rows = filtered
    return [ModelRegistryRead.from_orm_model(r) for r in rows]


async def get_model(session: AsyncSession, model_id: UUID) -> ModelRegistryRead | None:
    """Return a single registry entry by ID."""
    row = await session.get(ModelRegistry, model_id)
    if row is None:
        return None
    return ModelRegistryRead.from_orm_model(row)


async def update_model(
    session: AsyncSession,
    model_id: UUID,
    data: ModelRegistryUpdate,
) -> ModelRegistryRead | None:
    """Update an existing registry entry."""
    enc_key = _encryption_key()
    row = await session.get(ModelRegistry, model_id)
    if row is None:
        return None

    update_fields = data.model_dump(exclude_unset=True)
    if "public_dept_ids" in update_fields:
        update_fields["public_dept_ids"] = [str(v) for v in (update_fields.get("public_dept_ids") or [])] or None
    if "environments" in update_fields:
        update_fields["environments"] = [str(v).lower() for v in (update_fields.get("environments") or [])] or None

    # Handle API key separately
    plain_key = update_fields.pop("api_key", None)
    if plain_key and enc_key:
        row.api_key_secret_ref = encrypt_api_key(plain_key, enc_key)

    for field, value in update_fields.items():
        setattr(row, field, value)

    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ModelRegistryRead.from_orm_model(row)


async def delete_model(session: AsyncSession, model_id: UUID) -> bool:
    """Hard-delete a registry entry. Returns True if the row existed."""
    row = await session.get(ModelRegistry, model_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


def _is_fernet_token(value: str) -> bool:
    """Heuristic: Fernet tokens are base64-encoded and start with 'gAAAAA'."""
    return value.startswith("gAAAAA")


_kv_store_cache: dict[str, object] = {}


def _try_resolve_key_vault_secret(secret_name: str) -> str | None:
    """Attempt to fetch a secret from Azure Key Vault.

    Tries the model-service Key Vault config (MODEL_SERVICE_KEY_VAULT_URL) first,
    then falls back to the backend core config (AGENTCORE_KEY_VAULT_URL).

    Returns the secret value, or None if Key Vault is not configured or the
    secret cannot be retrieved.
    """
    from agentcore.services.settings.key_vault import KeyVaultConfig, KeyVaultSecretStore

    # Try model-service KV config first, then backend core KV config
    configs = [
        {
            "vault_url": os.getenv("MODEL_SERVICE_KEY_VAULT_URL", ""),
            "prefix": os.getenv("MODEL_SERVICE_KEY_VAULT_SECRET_PREFIX", "agentcore"),
        },
        {
            "vault_url": os.getenv("AGENTCORE_KEY_VAULT_URL", ""),
            "prefix": os.getenv("AGENTCORE_KEY_VAULT_SECRET_PREFIX", "agentcore"),
        },
    ]

    for cfg in configs:
        vault_url = (cfg["vault_url"] or "").strip()
        if not vault_url:
            continue
        cache_key = vault_url
        if cache_key not in _kv_store_cache:
            try:
                store = KeyVaultSecretStore.from_config(KeyVaultConfig(
                    vault_url=vault_url,
                    secret_prefix=cfg["prefix"],
                ))
                _kv_store_cache[cache_key] = store
            except Exception:
                _kv_store_cache[cache_key] = None
        store = _kv_store_cache[cache_key]
        if store is None:
            continue
        try:
            value = store.get_secret(secret_name)
            if value:
                return value
        except Exception as e:
            logger.debug("Key Vault lookup failed for '%s' at %s: %s", secret_name, vault_url, e)
            continue

    return None


async def get_decrypted_config(
    session: AsyncSession,
    model_id: UUID,
) -> dict | None:
    """Return the full config with decrypted API key.  Internal use only (components)."""
    enc_key = _encryption_key()
    row = await session.get(ModelRegistry, model_id)
    if row is None:
        return None

    config: dict = {
        "provider": row.provider,
        "model_name": row.model_name,
        "base_url": row.base_url,
        "environment": row.environment,
        "provider_config": row.provider_config or {},
        "capabilities": row.capabilities or {},
        "default_params": row.default_params or {},
    }

    secret_ref = row.api_key_secret_ref
    if secret_ref:
        if _is_fernet_token(secret_ref):
            # Locally encrypted with Fernet
            config["api_key"] = decrypt_api_key_with_fallback(secret_ref, enc_key)
        else:
            # Likely an Azure Key Vault secret name — try to resolve it
            resolved = _try_resolve_key_vault_secret(secret_ref)
            if resolved:
                config["api_key"] = resolved
            else:
                # Fall back to the model-service API which has Key Vault credentials
                svc_config = await _fetch_config_from_model_service(model_id)
                if svc_config and svc_config.get("api_key"):
                    config["api_key"] = svc_config["api_key"]
                else:
                    logger.warning(
                        "Cannot resolve api_key_secret_ref for model %s: "
                        "value '%s…' is neither a Fernet token nor resolvable via Key Vault or model-service. "
                        "Returning empty api_key.",
                        model_id,
                        secret_ref[:30],
                    )
                    config["api_key"] = ""
    else:
        config["api_key"] = ""

    return config


async def _fetch_config_from_model_service(model_id: UUID) -> dict | None:
    """Try to fetch decrypted config from the model-service microservice."""
    try:
        from agentcore.services.model_service_client import fetch_decrypted_model_config
        return await fetch_decrypted_model_config(str(model_id))
    except Exception as e:
        logger.debug("Model-service config fetch failed for %s: %s", model_id, e)
        return None


async def sync_model_costs(session: AsyncSession) -> None:
    """Sync model costs from the Langfuse database to the local model registry.

    Queries the 'observations' table in Langfuse to find the sum of cost
    for each model, matching by registry_model_id in metadata (or fallback model name).
    """
    import os
    from uuid import UUID
    from sqlalchemy import create_engine, text
    from sqlmodel import select
    from agentcore.services.database.models.model_registry.model import ModelRegistry

    rows = []

    # 1. Try to query Clickhouse first (used by newer Langfuse versions for observations)
    clickhouse_url = os.getenv("CLICKHOUSE_URL", "http://localhost:8123").strip()
    clickhouse_user = os.getenv("CLICKHOUSE_USER", "").strip()
    clickhouse_password = os.getenv("CLICKHOUSE_PASSWORD", "").strip()

    if not clickhouse_url.startswith("http"):
        clickhouse_url = f"http://{clickhouse_url}"

    try:
        import httpx
        # Clickhouse query to get total cost grouped by model and registry_model_id
        ch_query = """
            SELECT 
                metadata['registry_model_id'] AS registry_model_id,
                provided_model_name AS model_name,
                SUM(total_cost) AS total_cost
            FROM observations
            WHERE type = 'GENERATION' AND provided_model_name IS NOT NULL
            GROUP BY registry_model_id, model_name
            FORMAT JSON
        """
        auth = None
        if clickhouse_user and clickhouse_password:
            auth = (clickhouse_user, clickhouse_password)

        resp = httpx.post(clickhouse_url, auth=auth, data=ch_query, timeout=10.0)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            for item in data:
                rows.append((
                    item.get("registry_model_id") or "",
                    item.get("model_name") or "",
                    float(item.get("total_cost") or 0.0)
                ))
            logger.info("Successfully fetched %d rows from Clickhouse observations", len(rows))
    except Exception as e:
        logger.debug("Failed to query Clickhouse for model costs: %s", e)

    # 2. Fall back to Postgres if Clickhouse query returned no data or failed
    if not rows:
        langfuse_db_url = os.getenv("LANGFUSE_DB_URL", "").strip()
        if langfuse_db_url:
            try:
                engine = create_engine(langfuse_db_url, future=True)
                pg_query = """
                    SELECT 
                        COALESCE(
                            metadata->>'registry_model_id', 
                            metadata->'invocation_params'->>'registry_model_id'
                        ) AS registry_model_id,
                        model AS model_name,
                        SUM(COALESCE(calculated_total_cost, total_cost, 0)) AS total_cost
                    FROM observations
                    WHERE type = 'GENERATION'
                    GROUP BY registry_model_id, model
                """
                with engine.connect() as conn:
                    result = conn.execute(text(pg_query))
                    for r in result.fetchall():
                        rows.append((
                            r[0] or "",
                            r[1] or "",
                            float(r[2] or 0.0)
                        ))
                engine.dispose()
                logger.info("Successfully fetched %d rows from Postgres observations fallback", len(rows))
            except Exception as e:
                logger.error("Failed to query Langfuse Postgres database for model costs: %s", e)

    # Aggregate costs
    costs_by_id = {}
    costs_by_name = {}
    name_has_tagged_runs = {}

    for r_id, model_name, total_cost in rows:
        if r_id:
            try:
                costs_by_id[UUID(r_id)] = total_cost
                if model_name:
                    name_has_tagged_runs[model_name] = True
            except ValueError:
                pass
        if model_name:
            costs_by_name[model_name] = costs_by_name.get(model_name, 0.0) + total_cost

    # Update local model registry database
    try:
        stmt = select(ModelRegistry)
        db_result = await session.execute(stmt)
        models = db_result.scalars().all()

        updated_count = 0
        for model in models:
            cost = 0.0
            # Try to match by registry_model_id first
            if model.id in costs_by_id:
                cost = costs_by_id[model.id]
            # Fall back to model_name match ONLY if there are no registry-specific costs recorded for this name
            elif model.model_name in costs_by_name and not name_has_tagged_runs.get(model.model_name, False):
                cost = costs_by_name[model.model_name]

            if model.current_cost != cost:
                model.current_cost = cost
                session.add(model)
                updated_count += 1

        if updated_count > 0:
            await session.commit()
            logger.info("Successfully synced cost for %d models", updated_count)
    except Exception as e:
        logger.error("Failed to update model registry costs in database: %s", e)

