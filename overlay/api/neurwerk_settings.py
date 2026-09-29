# Modified by Neurwerk, 2026. This Dify-derived feature configuration retains
# Dify's Open Source License (Apache 2.0 with additional conditions).
# See LICENSES/Dify-LICENSE and NOTICE-CHANGES.md.
"""Small, independent configuration extension for Dify's pinned config model."""

from pydantic_settings import BaseSettings


class KeycloakConfig(BaseSettings):
    KEYCLOAK_OIDC_ISSUER_URL: str = ""
    KEYCLOAK_OIDC_CLIENT_ID: str = ""
    KEYCLOAK_OIDC_CLIENT_SECRET: str = ""
    KEYCLOAK_OIDC_STATE_MAX_AGE_SECONDS: int = 300
    ALLOW_SSO_REGISTER: bool = False
    ENFORCE_SINGLE_WORKSPACE: bool = True
    AUTO_SETUP_ADMIN_EMAIL: str = ""
    AUTO_SETUP_ADMIN_PASSWORD: str = ""
    DEFAULT_WORKSPACE_NAME: str = "default"
