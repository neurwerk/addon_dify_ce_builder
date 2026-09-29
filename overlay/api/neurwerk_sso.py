# Modified by Neurwerk, 2026. This Dify-derived Keycloak integration retains
# Dify's Open Source License (Apache 2.0 with additional conditions).
# See LICENSES/Dify-LICENSE and NOTICE-CHANGES.md.
"""Keycloak admission for the pinned Dify 1.17.1 OAuth application service."""

import hmac
import secrets
import urllib.parse
from types import MethodType

import httpx
import jwt
from flask import g, session
from itsdangerous import BadData, URLSafeTimedSerializer
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import delete, select

from libs.oauth import OAuth, OAuthUserInfo
from services.account_errors import OAuthProviderAuthorizationError, OAuthRegistrationError
from services.account_oauth_adapters import DifyOAuthProviderGateway
from services.account_oauth_service import AccountOAuthService
from services.entities.account_oauth_entities import OAuthCallbackCommand, OAuthIdentity


def _settings():
    from configs import dify_config

    return dify_config


class KeycloakOAuth(OAuth):
    def __init__(self):
        config = _settings()
        self.issuer = config.KEYCLOAK_OIDC_ISSUER_URL.rstrip("/")
        self.discovery = None
        super().__init__(
            config.KEYCLOAK_OIDC_CLIENT_ID,
            config.KEYCLOAK_OIDC_CLIENT_SECRET,
            config.CONSOLE_API_URL + "/console/api/oauth/authorize/keycloak",
        )

    def _discover(self):
        if self.discovery is None:
            response = httpx.get(self.issuer + "/.well-known/openid-configuration", timeout=10)
            response.raise_for_status()
            discovery = response.json()
            if discovery.get("issuer") != self.issuer or not all(
                isinstance(discovery.get(key), str) and discovery[key]
                for key in ("authorization_endpoint", "token_endpoint", "jwks_uri")
            ):
                raise ValueError("Keycloak OIDC discovery is invalid")
            self.discovery = discovery
        return self.discovery

    def get_authorization_url(
        self, invite_token=None, timezone=None, language=None, redirect_url=None
    ):
        nonce = secrets.token_urlsafe(32)
        session["keycloak_oauth_state"] = nonce
        state = {"nonce": nonce}
        for key, value in (
            ("invite_token", invite_token),
            ("timezone", timezone),
            ("language", language),
            ("redirect_url", redirect_url),
        ):
            if value:
                state[key] = value
        # The callback rejects invitations rather than using them to join a workspace.
        signed = URLSafeTimedSerializer(_settings().SECRET_KEY, salt="keycloak-oauth").dumps(state)
        return (
            self._discover()["authorization_endpoint"]
            + "?"
            + urllib.parse.urlencode(
                {
                    "client_id": self.client_id,
                    "redirect_uri": self.redirect_uri,
                    "response_type": "code",
                    "scope": "openid email profile",
                    "state": signed,
                }
            )
        )

    def get_access_token(self, code):
        response = httpx.post(
            self._discover()["token_endpoint"],
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
            },
            timeout=10,
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("Keycloak returned no access token")
        return token

    def get_raw_user_info(self, token):
        try:
            key = jwt.PyJWKClient(self._discover()["jwks_uri"]).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "email", "email_verified"]},
            )
        except jwt.PyJWTError as exc:
            raise ValueError("Keycloak token validation failed") from exc
        if claims.get("azp") != self.client_id:
            raise ValueError("Keycloak token was not issued to this client")
        return claims

    def _transform_user_info(self, raw_info):
        subject, email = raw_info.get("sub"), raw_info.get("email")
        if (
            not isinstance(subject, str)
            or not subject
            or not isinstance(email, str)
            or "@" not in email
        ):
            raise ValueError("Keycloak subject or email is invalid")
        if raw_info.get("email_verified") is not True:
            raise ValueError("Keycloak email is not verified")
        try:
            roles = TypeAdapter(list[str]).validate_python(raw_info["realm_access"]["roles"])
        except (KeyError, TypeError, ValidationError) as exc:
            raise ValueError("Keycloak roles are invalid") from exc
        g.keycloak_identity = OAuthIdentity(
            id=subject,
            name=str(raw_info.get("preferred_username") or "Dify"),
            email=email.strip().lower(),
        )
        g.keycloak_roles = frozenset(roles)
        return OAuthUserInfo(
            id=subject, name=g.keycloak_identity.name, email=g.keycloak_identity.email
        )


def decode_keycloak_state(state):
    if not state:
        raise ValueError("Keycloak OAuth state is required")
    try:
        payload = URLSafeTimedSerializer(_settings().SECRET_KEY, salt="keycloak-oauth").loads(
            state, max_age=_settings().KEYCLOAK_OIDC_STATE_MAX_AGE_SECONDS
        )
    except BadData as exc:
        raise ValueError("Keycloak OAuth state is invalid") from exc
    nonce = payload.get("nonce") if isinstance(payload, dict) else None
    expected = session.pop("keycloak_oauth_state", None)
    if (
        not isinstance(nonce, str)
        or not isinstance(expected, str)
        or not hmac.compare_digest(nonce, expected)
    ):
        raise ValueError("Keycloak OAuth state does not match this browser")
    return payload


def _synchronize_workspace(account_id, roles, session_factory):
    from models.account import Account, Tenant
    from services.account_service import TenantService

    with session_factory() as db_session:
        workspaces = db_session.scalars(select(Tenant).order_by(Tenant.created_at).limit(2)).all()
        if len(workspaces) != 1:
            raise OAuthRegistrationError("Exactly one Dify workspace is required")
        account = db_session.get(Account, account_id)
        if account is None:
            raise OAuthRegistrationError("Dify account not found")
        TenantService.create_tenant_member(
            workspaces[0], account, db_session, role="admin" if "dify-admin" in roles else "editor"
        )
        account.set_current_tenant_with_session(workspaces[0], session=db_session)
        db_session.commit()


def configure_oauth(service: AccountOAuthService, session_factory):
    """Extend upstream's OAuth service without replacing its locks or other providers."""
    from extensions.ext_application_services import application_services
    from libs.datetime_utils import naive_utc_now
    from models.account import AccountIntegrate, AccountStatus, TenantAccountJoin
    from services.account_service import AccountService
    from services.account_errors import OAuthSeatsLimitExceededError
    from services.errors.account import SeatsLimitExceededError

    if not (_settings().KEYCLOAK_OIDC_ISSUER_URL and _settings().KEYCLOAK_OIDC_CLIENT_ID):
        return service

    class Gateway(DifyOAuthProviderGateway):
        def get_identity(self, code):
            identity = super().get_identity(code)
            if identity.email == _settings().AUTO_SETUP_ADMIN_EMAIL.lower():
                raise OAuthProviderAuthorizationError(
                    "The break-glass owner cannot use Keycloak SSO"
                )
            if "dify-user" not in g.keycloak_roles:
                with session_factory() as db_session:
                    integration = db_session.scalar(
                        select(AccountIntegrate).where(
                            AccountIntegrate.provider == "keycloak",
                            AccountIntegrate.open_id == identity.id,
                        )
                    )
                    if integration is not None:
                        db_session.execute(
                            delete(TenantAccountJoin).where(
                                TenantAccountJoin.account_id == integration.account_id
                            )
                        )
                        db_session.commit()
                        application_services().accounts.authentication.logout(
                            integration.account_id
                        )
                raise OAuthProviderAuthorizationError("You are not authorized for Dify")
            return identity

    service._providers["keycloak"] = Gateway(provider_name="keycloak", client=KeycloakOAuth())
    original_resolve = service._resolve_account
    original_complete = service.complete_authorization
    original_register_policy = service._registration_policy
    original_registration = service._registration
    original_workspaces = service._workspaces

    def resolve(self, provider, identity):
        if provider != "keycloak":
            return original_resolve(provider, identity)
        account_id = self._integrations.find_account_id(provider=provider, open_id=identity.id)
        email_account = self._accounts.find_by_email(identity.email)
        if account_id is None and email_account is not None:
            raise OAuthRegistrationError("This email is not linked to the Keycloak identity")
        if account_id is not None:
            account = self._accounts.get(account_id)
            if email_account is not None and account is not None and email_account.id != account.id:
                raise OAuthRegistrationError(
                    "Keycloak subject and email identify different accounts"
                )
            return account
        return None

    def complete(self, command: OAuthCallbackCommand):
        if command.provider != "keycloak":
            return original_complete(command)
        if command.invite_token:
            raise OAuthRegistrationError("Keycloak invitations are not supported")
        result = original_complete(command)
        identity = g.keycloak_identity
        account_id = self._integrations.find_account_id(provider="keycloak", open_id=identity.id)
        if account_id is None:
            raise OAuthRegistrationError("Keycloak account binding is missing")
        _synchronize_workspace(account_id, g.keycloak_roles, session_factory)
        return result

    class Policy:
        def is_registration_allowed(self):
            if hasattr(g, "keycloak_identity"):
                return _settings().ALLOW_SSO_REGISTER
            return original_register_policy.is_registration_allowed()

        def get_freeze_type(self, email):
            return original_register_policy.get_freeze_type(email)

        def is_creation_allowed(self):
            if hasattr(g, "keycloak_identity"):
                return False
            return original_register_policy.is_creation_allowed()

    class Workspaces:
        def create_owner_workspace(self, account_id):
            return original_workspaces.create_owner_workspace(account_id)

        def try_join_default_workspace(self, account_id):
            if hasattr(g, "keycloak_identity"):
                _synchronize_workspace(account_id, g.keycloak_roles, session_factory)
                return
            return original_workspaces.try_join_default_workspace(account_id)

    class Registration:
        def register(self, registration):
            if not hasattr(g, "keycloak_identity"):
                return original_registration.register(registration)
            if not _settings().ALLOW_SSO_REGISTER:
                raise OAuthRegistrationError("SSO registration is disabled")
            with session_factory() as db_session:
                try:
                    account = AccountService.create_account(
                        email=registration.email,
                        name=registration.name,
                        interface_language=registration.language,
                        password=None,
                        is_setup=True,
                        timezone=registration.timezone,
                        ip_address=registration.ip_address,
                        check_normalized_email=True,
                        session=db_session,
                    )
                    account.status = AccountStatus.ACTIVE
                    account.initialized_at = naive_utc_now()
                    db_session.commit()
                except SeatsLimitExceededError as exc:
                    db_session.rollback()
                    raise OAuthSeatsLimitExceededError from exc
                except Exception as exc:
                    db_session.rollback()
                    raise OAuthRegistrationError("Keycloak registration failed") from exc
                return account.id

    def provision_existing(self, account_id, account_claim):
        if hasattr(g, "keycloak_identity"):
            account_claim.ensure_owned()
            _synchronize_workspace(account_id, g.keycloak_roles, session_factory)
            account_claim.ensure_owned()
            return
        return original_provision(account_id, account_claim)

    original_provision = service._provision_owner_workspace_if_required
    service._resolve_account = MethodType(resolve, service)
    service.complete_authorization = MethodType(complete, service)
    service._provision_owner_workspace_if_required = MethodType(provision_existing, service)
    service._registration_policy = Policy()
    service._registration = Registration()
    service._workspace_policy = service._registration_policy
    service._workspaces = Workspaces()
    return service
