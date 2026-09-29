# SPDX-License-Identifier: MIT
"""Apply guarded Keycloak hooks to the pinned Dify 1.17.1 source."""

import json
import sys
from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise RuntimeError(f"Unexpected Dify source in {path}: {old[:70]!r}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def patch_api(root: Path) -> None:
    config = root / "configs/app_config.py"
    replace_once(
        config,
        "from .feature import FeatureConfig\n",
        "from .feature import FeatureConfig\nfrom neurwerk_settings import KeycloakConfig\n",
    )
    replace_once(config, "    FeatureConfig,\n", "    FeatureConfig,\n    KeycloakConfig,\n")

    extension = root / "extensions/ext_application_services.py"
    replace_once(
        extension,
        "    policy = DeploymentOAuthPolicyGateway(\n        billing_enabled=deployment_edition == DeploymentEdition.CLOUD,\n    )\n    return AccountOAuthService(\n",
        "    policy = DeploymentOAuthPolicyGateway(\n        billing_enabled=deployment_edition == DeploymentEdition.CLOUD,\n    )\n    from neurwerk_sso import build_oauth_service\n\n    return build_oauth_service(\n",
    )
    replace_once(
        extension,
        "        supported_languages=languages,\n        now=naive_utc_now,\n    )\n",
        "        supported_languages=languages,\n        now=naive_utc_now,\n        session_factory=database_client,\n    )\n",
    )

    controller = root / "controllers/console/auth/oauth.py"
    replace_once(
        controller,
        "        oauth_state = decode_oauth_state(req_data.state)\n",
        "        if provider == 'keycloak':\n"
        "            from neurwerk_sso import decode_keycloak_state\n"
        "            try:\n"
        "                oauth_state = decode_keycloak_state(req_data.state)\n"
        "            except ValueError:\n"
        "                return _signin_redirect('Keycloak OAuth state is invalid.')\n"
        "        else:\n"
        "            oauth_state = decode_oauth_state(req_data.state)\n",
    )


def patch_web(root: Path) -> None:
    form = root / "app/signin/normal-form.tsx"
    replace_once(
        form,
        "import SocialAuth from './components/social-auth'\n",
        "import SocialAuth from './components/social-auth'\nimport SsoRedirect from './components/sso-redirect'\n",
    )
    replace_once(
        form,
        "  if (isLoading) {\n",
        "  if (hasSocialLogin && !isLoading && !isInviteLink && !searchParams.get('method') && !searchParams.get('message'))\n"
        "    return <SsoRedirect />\n\n  if (isLoading) {\n",
    )
    social = root / "app/signin/components/social-auth.tsx"
    replace_once(
        social,
        "      <a className={cn(buttonVariants(), 'w-full')} href={getOAuthLink('/oauth/login/github')}>\n"
        "        <span aria-hidden=\"true\" className={cn(style.githubIcon, 'size-5')} />\n"
        "        <span className=\"truncate leading-normal\">{t(($) => $.withGitHub, { ns: 'login' })}</span>\n"
        "      </a>\n"
        "      <a className={cn(buttonVariants(), 'w-full')} href={getOAuthLink('/oauth/login/google')}>\n"
        "        <span aria-hidden=\"true\" className={cn(style.googleIcon, 'size-5')} />\n"
        "        <span className=\"truncate leading-normal\">{t(($) => $.withGoogle, { ns: 'login' })}</span>\n"
        "      </a>\n",
        "      <a className={cn(buttonVariants(), 'w-full')} href={getOAuthLink('/oauth/login/keycloak')}>\n"
        "        <span className=\"truncate leading-normal\">{t(($) => $.withKeycloak, { ns: 'login' })}</span>\n"
        "      </a>\n",
    )
    replace_once(social, "import style from '../page.module.css'\n", "")
    login = root / "i18n/en-US/login.json"
    strings = json.loads(login.read_text(encoding="utf-8"))
    strings.update(
        {
            "withKeycloak": "Continue with Keycloak",
            "redirectingToSSO": "Redirecting to sign in in {{seconds}} seconds...",
            "redirectNow": "Sign in now",
            "useLoginForm": "Use the login form instead",
        }
    )
    login.write_text(json.dumps(strings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in {"api", "web"}:
        raise SystemExit("Usage: patch_dify.py api|web /path/to/target")
    (patch_api if sys.argv[1] == "api" else patch_web)(Path(sys.argv[2]))
