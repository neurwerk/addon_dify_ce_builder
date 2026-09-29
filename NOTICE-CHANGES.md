# Notice of Changes

This image extends Dify Community Edition 1.17.1, under Dify's Open Source
License (Apache 2.0 with additional conditions). Neurwerk's separate builder
and tooling are MIT licensed; Dify-derived integration files retain Dify's terms.

The pinned API image retains upstream Dify's OAuth application service and
account management. Its existing `4474872b0ee6` concurrent-index migration
overlay remains retry-safe; Dify's PostgreSQL 18 UUID fix is now upstream.
`overlay/scripts/patch_dify.py` makes checked, narrow
source changes to `configs/app_config.py`,
`extensions/ext_application_services.py`, and
`controllers/console/auth/oauth.py` to register the Keycloak OIDC provider,
validate browser-bound state, and enforce a single workspace. It also adds
`overlay/api/neurwerk_sso.py`, `overlay/api/neurwerk_settings.py`, and the model
provider setup helper. The Web build
patches upstream `app/signin/normal-form.tsx`,
`app/signin/components/social-auth.tsx`, and `i18n/en-US/login.json` before
compilation, and adds `app/signin/components/sso-redirect.tsx` for Keycloak login.
Do not remove or alter Dify's logo or copyright notices.

The bundled upstream OpenAI-compatible model plugin 0.0.56 is unchanged and
Apache 2.0 licensed. See `THIRD_PARTY_NOTICES.md` for provenance and licensing.
