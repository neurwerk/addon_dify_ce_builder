# SPDX-License-Identifier: MIT
"""Automated model provider setup script.

Bootstraps a Flask app context, requires the deployment's sole tenant, and
provisions model credentials for each configured provider with upsert semantics.

Environment variables:
    LLM_PROXY_API_KEY         — AgentGateway credential (required)
    DIFY_BOOTSTRAP_LOCK_TIMEOUT_SECONDS — Maximum advisory-lock wait
                                          (optional, default 1800)
    MODEL_PROVIDER_CREDENTIALS — JSON dict::

        {
          "<provider>": {
            "model_type": "llm" | "text-embedding" | ...,
            "model": "<model-name>",
            "credentials": { ... },
            "as_default": true | false   (optional, default false)
          },
          ...
        }

    MODEL_SETTINGS (optional) — JSON dict overriding defaults per provider:
        {
          "<provider>": {
            "enabled": true | false,
            "default_model_type": "llm"
          }
        }
"""

import json
import logging
import math
import os
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_BOOTSTRAP_LOCK_KEY = 0x44494659424F4F54  # "DIFYBOOT" as a PostgreSQL bigint
_BOOTSTRAP_LOCK_TIMEOUT_SECONDS = 1800
_BOOTSTRAP_LOCK_POLL_INTERVAL_SECONDS = 1


def _bootstrap_lock_timeout_seconds() -> float:
    """Return the configured bounded wait for the bootstrap advisory lock."""
    raw = os.environ.get(
        "DIFY_BOOTSTRAP_LOCK_TIMEOUT_SECONDS",
        str(_BOOTSTRAP_LOCK_TIMEOUT_SECONDS),
    )
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise ValueError("DIFY_BOOTSTRAP_LOCK_TIMEOUT_SECONDS must be a positive number") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("DIFY_BOOTSTRAP_LOCK_TIMEOUT_SECONDS must be a positive number")
    return timeout


@contextmanager
def _bootstrap_lock(timeout_seconds: float | None = None):
    """Hold the process-wide bootstrap lock on a dedicated DB connection."""
    from extensions.ext_database import db
    from sqlalchemy import text

    if timeout_seconds is None:
        timeout_seconds = _bootstrap_lock_timeout_seconds()
    deadline = time.monotonic() + timeout_seconds
    acquire_statement = text("SELECT pg_try_advisory_lock(CAST(:lock_key AS BIGINT))")
    release_statement = text("SELECT pg_advisory_unlock(CAST(:lock_key AS BIGINT))")

    with db.engine.connect() as connection:
        acquired = False
        try:
            while True:
                acquired = bool(
                    connection.scalar(
                        acquire_statement,
                        {"lock_key": _BOOTSTRAP_LOCK_KEY},
                    )
                )
                # The lock is session-level, so it survives this commit while
                # avoiding an idle transaction during the potentially long setup.
                connection.commit()
                if acquired:
                    logger.info("Acquired Dify bootstrap lock.")
                    break

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "Could not acquire the Dify bootstrap lock within "
                        f"{timeout_seconds:g} seconds; another bootstrap process "
                        "may still be running."
                    )
                time.sleep(min(_BOOTSTRAP_LOCK_POLL_INTERVAL_SECONDS, remaining))

            yield
        finally:
            if acquired:
                try:
                    released = bool(
                        connection.scalar(
                            release_statement,
                            {"lock_key": _BOOTSTRAP_LOCK_KEY},
                        )
                    )
                    connection.commit()
                except Exception:
                    # Closing the physical DB session is the safe fallback for a
                    # session-level lock when an explicit unlock cannot complete.
                    connection.invalidate()
                    raise
                if not released:
                    connection.invalidate()
                    raise RuntimeError("Dify bootstrap lock release was not confirmed")
                logger.info("Released Dify bootstrap lock.")


def _require_single_tenant(tenants):
    """Return the sole tenant or reject an inconsistent workspace state."""
    tenant_count = len(tenants)
    if tenant_count != 1:
        raise RuntimeError(
            "Model provider setup requires exactly one tenant after auto-setup; "
            f"found {tenant_count}. Refusing to select a workspace implicitly."
        )
    return tenants[0]


def _resolve_tenant():
    """Return the deployment's sole tenant."""
    from extensions.ext_database import db
    from models.account import Tenant
    from sqlalchemy import select

    tenants = db.session.scalars(select(Tenant).order_by(Tenant.created_at.asc())).all()
    tenant = _require_single_tenant(tenants)
    logger.info("Resolved tenant '%s' (id=%s).", tenant.name, tenant.id)
    return tenant


def _require_llm_proxy_api_key() -> str:
    """Return the configured AgentGateway credential or fail closed."""
    api_key = os.environ.get("LLM_PROXY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "LLM_PROXY_API_KEY is required when model provider setup is enabled; "
            "refusing to continue without provider authentication."
        )
    return api_key


def _load_credentials_config():
    """Read and validate MODEL_PROVIDER_CREDENTIALS from env."""
    raw = os.environ.get("MODEL_PROVIDER_CREDENTIALS")
    if not raw:
        logger.info("MODEL_PROVIDER_CREDENTIALS not set — nothing to provision.")
        return {}

    try:
        cfg = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise ValueError("MODEL_PROVIDER_CREDENTIALS is not valid JSON") from exc

    if not isinstance(cfg, dict):
        raise ValueError("MODEL_PROVIDER_CREDENTIALS must be a JSON object")
    return cfg


def _load_settings_config():
    """Read and validate the optional MODEL_SETTINGS object from env."""
    raw = os.environ.get("MODEL_SETTINGS")
    if not raw:
        return {}

    try:
        settings = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise ValueError("MODEL_SETTINGS is not valid JSON") from exc

    if not isinstance(settings, dict):
        raise ValueError("MODEL_SETTINGS must be a JSON object")
    return settings


def _provider_entries(credentials_config):
    """Validate and normalize provider definitions keyed by provider name."""
    entries = []
    for provider_name, config in credentials_config.items():
        if not isinstance(config, dict):
            raise ValueError(f"Provider configuration for {provider_name} must be an object")
        entry = dict(config)
        entry.setdefault("provider", provider_name)
        for field in ("provider", "model", "credentials"):
            if field not in entry:
                raise ValueError(f"Provider configuration for {provider_name} is missing {field}")
        if not isinstance(entry["provider"], str) or not entry["provider"].strip():
            raise ValueError(f"Provider configuration for {provider_name} has an invalid provider")
        if not isinstance(entry["model"], str) or not entry["model"].strip():
            raise ValueError(f"Provider configuration for {provider_name} has an invalid model")
        if not isinstance(entry["credentials"], dict):
            raise ValueError(f"Provider configuration for {provider_name} has invalid credentials")
        entries.append(entry)
    return entries


def _resolve_credential_id(
    tenant_id: str, provider: str, model: str, model_type: str
) -> str | None:
    """Resolve the credential id from the provider tables.

    Plugin-based providers are stored under the full plugin provider id
    (langgenius/<name>/<name>) even when the short name was passed in,
    so match both forms.
    """
    from extensions.ext_database import db
    from sqlalchemy import desc, select
    from models.provider import ProviderModel, ProviderModelCredential

    provider_candidates = [provider, f"langgenius/{provider}/{provider}"]

    pm = db.session.scalar(
        select(ProviderModel)
        .where(
            ProviderModel.tenant_id == tenant_id,
            ProviderModel.provider_name.in_(provider_candidates),
            ProviderModel.model_name == model,
            ProviderModel.model_type == model_type,
        )
        .limit(1)
    )
    if pm and pm.credential_id:
        return pm.credential_id

    cred = db.session.scalar(
        select(ProviderModelCredential)
        .where(
            ProviderModelCredential.tenant_id == tenant_id,
            ProviderModelCredential.provider_name.in_(provider_candidates),
            ProviderModelCredential.model_name == model,
            ProviderModelCredential.model_type == model_type,
        )
        .order_by(desc(ProviderModelCredential.created_at))
        .limit(1)
    )
    return cred.id if cred else None


def _provision(tenant_id: str, provider_config: dict):
    """Provision a single provider's model credentials (upsert semantics).

    Steps:
      1. Resolve credential id from the DB (deterministic — do not rely on
         get_model_credential's response shape)
      2. Update if found, otherwise create (tolerating duplicate-credential
         errors from interrupted previous runs)
      3. Add it to the active model list (idempotent — skips if present)
      4. Enable the model
      5. Optionally set it as the default LLM model
    """
    from services.model_provider_service import ModelProviderService

    svc = ModelProviderService()
    provider = provider_config["provider"]
    model_type = provider_config.get("model_type", "llm")
    model = provider_config["model"]
    credentials = provider_config["credentials"]
    credential_name = provider_config.get("credential_name")
    as_default = provider_config.get("as_default", False)

    logger.info(
        "Upserting %s/%s/%s for tenant %s ...",
        provider,
        model_type,
        model,
        tenant_id,
    )

    cred_id = _resolve_credential_id(tenant_id, provider, model, model_type)
    if cred_id:
        logger.info("Credential already exists (%s) — updating.", cred_id)
        try:
            svc.update_model_credential(
                tenant_id=tenant_id,
                provider=provider,
                model_type=model_type,
                model=model,
                credentials=credentials,
                credential_id=cred_id,
                credential_name=credential_name,
            )
        except ValueError as exc:
            # Dify's update path can raise the same duplicate-name error when
            # the stored credential already matches. The credential exists
            # either way — continue to add-to-list/enable/default.
            if "same credential" not in str(exc):
                raise
            logger.info("Update refused as duplicate — credential already in place, continuing.")
    else:
        try:
            svc.create_model_credential(
                tenant_id=tenant_id,
                provider=provider,
                model_type=model_type,
                model=model,
                credentials=credentials,
                credential_name=credential_name,
            )
        except ValueError as exc:
            # Idempotency: a previous run may have created the credential but
            # failed before finishing (add-to-list/enable/default). Treat the
            # duplicate-credential error as "already exists" and continue.
            if "same credential" not in str(exc):
                raise
            logger.info("Credential already exists daemon-side — continuing with id resolution.")

        cred_id = _resolve_credential_id(tenant_id, provider, model, model_type)
        if not cred_id:
            raise RuntimeError(
                "Could not resolve credential id for "
                f"{provider}/{model_type}/{model} after provisioning"
            )

    # 3. Add credential to active model list (tolerate "already in list")
    try:
        svc.add_model_credential_to_model_list(
            tenant_id=tenant_id,
            provider=provider,
            model_type=model_type,
            model=model,
            credential_id=cred_id,
        )
    except ValueError as exc:
        if "same credential" not in str(exc):
            raise
        logger.info("Credential already in model list — continuing.")

    # 4. Enable the model (tolerate "already enabled")
    try:
        svc.enable_model(
            tenant_id=tenant_id,
            provider=provider,
            model=model,
            model_type=model_type,
        )
    except ValueError as exc:
        if "same credential" not in str(exc) and "already" not in str(exc):
            raise
        logger.info("Model already enabled — continuing.")

    # 5. Optionally set as default LLM
    if as_default:
        logger.info("Setting %s/%s as default %s model.", provider, model, model_type)
        svc.update_default_model_of_model_type(
            tenant_id=tenant_id,
            model_type=model_type,
            provider=provider,
            model=model,
        )

    logger.info("Successfully provisioned %s/%s/%s.", provider, model_type, model)


_PLUGIN_ID = "langgenius/openai_api_compatible"
_PLUGIN_PKG = "/app/api/plugins-offline/langgenius-openai_api_compatible_0.0.56.difypkg"
_PLUGIN_INSTALL_TIMEOUT = 900  # seconds
_PLUGIN_INSTALL_POLL_INTERVAL = 5  # seconds
_DAEMON_WAIT_TIMEOUT = 300  # seconds — cold-start: daemon may still be booting


def _wait_for_daemon(tenant_id: str) -> bool:
    """Wait until the plugin daemon answers management calls (cold-start race).

    Returns True when ready, False on timeout (caller should defer).
    """
    from core.plugin.plugin_service import PluginService

    deadline = time.time() + _DAEMON_WAIT_TIMEOUT
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            PluginService.list(tenant_id)
            logger.info("Plugin daemon is ready (attempt %d).", attempt)
            return True
        except Exception as exc:
            logger.info("Waiting for plugin daemon (attempt %d): %s", attempt, exc)
            time.sleep(5)
    logger.warning("Plugin daemon not ready after %ds — deferring.", _DAEMON_WAIT_TIMEOUT)
    return False


def _ensure_tenant_rsa(tenant) -> None:
    """Ensure the tenant has an RSA key pair for credential encryption.

    Upstream generates this at tenant creation (account_service). Our
    _auto_setup predates that, and any credential decrypt path
    (ProviderManager.get_configurations) fails without it — in the console
    UI as well as here. Idempotent.
    """
    from extensions.ext_database import db
    from libs import rsa
    from libs.rsa import PrivkeyNotFoundError

    try:
        rsa.get_decrypt_decoding(tenant.id)
        logger.info("Tenant RSA key pair exists.")
        return
    except PrivkeyNotFoundError:
        pass

    logger.info("Generating RSA key pair for tenant %s ...", tenant.id)
    tenant.encrypt_public_key = rsa.generate_key_pair(tenant.id)
    db.session.commit()
    logger.info("RSA key pair generated (private key persisted to storage).")


def _ensure_plugins(tenant_id: str) -> None:
    """Install the bundled openai_api_compatible plugin if not already present."""
    from core.plugin.entities.plugin_daemon import PluginInstallTaskStatus
    from core.plugin.plugin_service import PluginService

    plugins = PluginService.list(tenant_id)
    if any(p.plugin_id == _PLUGIN_ID for p in plugins):
        logger.info("Plugin '%s' is already installed — skipping.", _PLUGIN_ID)
        return

    if not os.path.isfile(_PLUGIN_PKG):
        raise FileNotFoundError(f"Plugin package not found at {_PLUGIN_PKG}")

    with open(_PLUGIN_PKG, "rb") as fh:
        pkg_bytes = fh.read()
    logger.info(
        "Uploading %s (%d bytes) to plugin daemon for tenant %s ...",
        _PLUGIN_ID,
        len(pkg_bytes),
        tenant_id,
    )

    try:
        decoded = PluginService.upload_pkg(tenant_id, pkg_bytes)
    except Exception as exc:
        raise RuntimeError("Plugin upload failed") from exc

    logger.info("Uploaded %s → unique_identifier=%s", _PLUGIN_ID, decoded.unique_identifier)

    try:
        result = PluginService.install_from_local_pkg(tenant_id, [decoded.unique_identifier])
    except Exception as exc:
        raise RuntimeError("Plugin install request failed") from exc

    if result.all_installed:
        logger.info("Plugin '%s' already fully installed on daemon side — done.", _PLUGIN_ID)
        return

    if result.task and result.task.status == PluginInstallTaskStatus.Success:
        logger.info("Plugin '%s' install task already succeeded — done.", _PLUGIN_ID)
        return

    task_id = result.task_id
    logger.info("Waiting for install task %s ...", task_id)

    deadline = time.time() + _PLUGIN_INSTALL_TIMEOUT
    while time.time() < deadline:
        time.sleep(_PLUGIN_INSTALL_POLL_INTERVAL)
        try:
            task = PluginService.fetch_install_task(tenant_id, task_id)
        except Exception as exc:
            logger.warning("Failed to fetch install task %s: %s", task_id, exc)
            continue

        logger.info(
            "Task %s status=%s (completed %d/%d)",
            task_id,
            task.status,
            task.completed_plugins,
            task.total_plugins,
        )

        if task.status == PluginInstallTaskStatus.Success:
            logger.info("Plugin '%s' installed successfully.", _PLUGIN_ID)
            return
        if task.status == PluginInstallTaskStatus.Failed:
            messages = [p.message for p in task.plugins if p.message]
            raise RuntimeError(
                "Plugin install failed: " + ("; ".join(messages) if messages else "unknown error")
            )

    raise TimeoutError(
        "Plugin install task %s did not complete within %d seconds"
        % (task_id, _PLUGIN_INSTALL_TIMEOUT)
    )


def _run() -> None:
    """Run every required setup stage inside the Dify application context."""
    from app_factory import create_flask_app_with_configs, initialize_extensions, _auto_setup

    app = create_flask_app_with_configs()
    with app.app_context():
        initialize_extensions(app)

    with app.app_context():
        with _bootstrap_lock():
            # Recheck all state only after serializing concurrent pod startups.
            _auto_setup(app)
            tenant = _resolve_tenant()
            _require_llm_proxy_api_key()

            # ---- Tenant RSA key pair (required for any credential decrypt path) ----
            _ensure_tenant_rsa(tenant)

            # ---- Wait for plugin daemon (cold-start race on fresh clusters) ----
            if not _wait_for_daemon(tenant.id):
                raise RuntimeError("Plugin daemon did not become ready before timeout")

            # ---- Ensure bundled plugins are installed (needed for providers) ----
            _ensure_plugins(tenant.id)

            _load_settings_config()

            # ---- Provision each configured provider ----
            credentials_config = _load_credentials_config()
            for entry in _provider_entries(credentials_config):
                _provision(tenant.id, entry)

            logger.info("Model provider setup complete.")


def main() -> None:
    """Exit nonzero whenever required setup does not converge."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        _run()
    except Exception as exc:
        logger.exception("Model provider setup failed: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
