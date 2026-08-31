# Modified by Neurwerk, 2025-2026: permit controlled first-login SSO creation.
# This file contains Dify-derived method bodies and remains under the Dify Open
# Source License, based on Apache License 2.0 with additional conditions. See
# LICENSES/Dify-LICENSE and NOTICE-CHANGES.md.
"""Runtime patch for services.account_service (Keycloak SSO support).

Adds `bypass_register_check` support to `AccountService.create_account` and
`RegisterService.register`, so first-time SSO users can be registered even when
public self-registration (`is_allow_register`) is disabled.

Applied via `apply()` from `app_factory.create_flask_app_with_configs()`.

IMPORTANT: The method bodies below are faithful copies of the upstream
langgenius/dify-api 1.15.0 implementations with ONLY the
`bypass_register_check` parameter added (and passed through to
`create_account`). On upstream version bumps, re-copy the bodies from
`api/services/account_service.py` and re-add the parameter.
"""

import base64
import logging
import secrets

from sqlalchemy.orm import Session, scoped_session

from configs import dify_config
from services.errors.account import AccountRegisterError
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


def _patched_create_account(
    email: str,
    name: str,
    interface_language: str,
    password: str | None = None,
    interface_theme: str = "light",
    is_setup: bool | None = False,
    timezone: str | None = None,
    bypass_register_check: bool = False,
    *,
    session: scoped_session | Session,
):
    """Upstream 1.15.0 AccountService.create_account + bypass_register_check."""
    from constants.languages import language_timezone_mapping
    from libs.helper import timezone as validate_timezone
    from libs.password import hash_password, valid_password
    from models.account import Account
    from services.billing_service import BillingService

    if not FeatureService.get_system_features().is_allow_register and not is_setup and not bypass_register_check:
        from controllers.console.error import AccountNotFound

        raise AccountNotFound()

    if dify_config.BILLING_ENABLED and BillingService.is_email_in_freeze(email):
        raise AccountRegisterError(
            description=(
                "This email account has been deleted within the past "
                "30 days and is temporarily unavailable for new account registration"
            )
        )

    password_to_set = None
    salt_to_set = None
    if password:
        valid_password(password)

        # generate password salt
        salt = secrets.token_bytes(16)
        base64_salt = base64.b64encode(salt).decode()

        # encrypt password with salt
        password_hashed = hash_password(password, salt)
        base64_password_hashed = base64.b64encode(password_hashed).decode()

        password_to_set = base64_password_hashed
        salt_to_set = base64_salt

    resolved_timezone = language_timezone_mapping.get(interface_language, "UTC")
    if timezone is not None:
        resolved_timezone = validate_timezone(timezone)

    account = Account(
        name=name,
        email=email,
        password=password_to_set,
        password_salt=salt_to_set,
        interface_language=interface_language,
        interface_theme=interface_theme,
        timezone=resolved_timezone,
    )

    session.add(account)
    session.commit()
    return account


def _patched_register(
    cls,
    email: str,
    name: str,
    password: str | None = None,
    open_id: str | None = None,
    provider: str | None = None,
    language: str | None = None,
    status=None,
    is_setup: bool | None = False,
    create_workspace_required: bool | None = True,
    timezone: str | None = None,
    bypass_register_check: bool = False,
    *,
    session: scoped_session | Session,
):
    """Upstream 1.15.0 RegisterService.register + bypass_register_check passthrough."""
    from constants.languages import get_valid_language
    from events.tenant_event import tenant_was_created
    from libs.datetime_utils import naive_utc_now
    from models.account import AccountStatus
    from services.account_service import AccountService, TenantService, _try_join_enterprise_default_workspace
    from services.errors.workspace import WorkSpaceNotAllowedCreateError

    session.begin_nested()
    try:
        interface_language = get_valid_language(language)
        account = AccountService.create_account(
            email=email,
            name=name,
            interface_language=interface_language,
            password=password,
            is_setup=is_setup,
            timezone=timezone,
            bypass_register_check=bypass_register_check,
            session=session,
        )
        account.status = status or AccountStatus.ACTIVE
        account.initialized_at = naive_utc_now()

        if open_id is not None and provider is not None:
            AccountService.link_account_integrate(provider, open_id, account, session=session)

        if (
            FeatureService.get_system_features().is_allow_create_workspace
            and create_workspace_required
            and FeatureService.get_system_features().license.workspaces.is_available()
        ):
            try:
                tenant = TenantService.create_tenant(f"{account.name}'s Workspace", session=session)
                TenantService.create_tenant_member(tenant, account, session, role="owner")
                account.current_tenant = tenant
                tenant_was_created.send(tenant)
            except Exception:
                _try_join_enterprise_default_workspace(str(account.id))
                raise

        session.commit()

        _try_join_enterprise_default_workspace(str(account.id))
    except WorkSpaceNotAllowedCreateError:
        session.rollback()
        logger.exception("Register failed")
        raise AccountRegisterError("Workspace is not allowed to create.")
    except AccountRegisterError as are:
        session.rollback()
        logger.exception("Register failed")
        raise are
    except Exception as e:
        session.rollback()
        logger.exception("Register failed")
        raise AccountRegisterError(f"Registration failed: {e}") from e

    return account


def apply() -> None:
    """Monkey-patch AccountService.create_account and RegisterService.register."""
    from services.account_service import AccountService, RegisterService

    AccountService.create_account = staticmethod(_patched_create_account)  # type: ignore[method-assign]
    RegisterService.register = classmethod(_patched_register)  # type: ignore[method-assign]
    logger.info("Applied account_service patch: bypass_register_check support enabled")
