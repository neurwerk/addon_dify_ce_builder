# Notice of Changes

This repository contains modifications and additions for Dify Community
Edition (CE). Dify-derived files remain under the Dify Open Source License in
`LICENSES/Dify-LICENSE`. Neurwerk-owned additions and build tooling are under
the MIT license in `LICENSE`.

Original Dify work: Copyright 2025 LangGenius, Inc.
Neurwerk changes: Copyright 2025-2026 Neurwerk.

## Changes

- Added `KeycloakOAuth` provider to the Dify-derived
  `overlay/api/libs/oauth.py` with
  signed-token validation, browser-bound state, verified-email admission, and
  Keycloak role extraction
- Modified `overlay/api/controllers/console/auth/oauth.py` to register the
  Keycloak provider, isolate the break-glass owner, and reconcile editor/admin
  access in the single workspace
- Modified `overlay/api/configs/feature/__init__.py` with the Keycloak OIDC
  feature configuration
- Modified `overlay/api/app_factory.py` to apply the account-service patch
- Added Dify-derived `overlay/api/services/account_service_patch.py`: runtime
  patch adding `bypass_register_check` to `AccountService.create_account` /
  `RegisterService.register` so first-time SSO users can register while public
  self-registration stays disabled; applied from the modified
  `overlay/api/app_factory.py`
- Modified
  `overlay/api/migrations/versions/2025_06_06_1424-4474872b0ee6_workflow_draft_varaibles_add_node_execution_id.py`
  (`4474872b0ee6`) to make its concurrent PostgreSQL index
  creation retry-safe: an exact valid index is reused, an invalid remnant is
  replaced, and a conflicting valid definition fails closed
- Modified
  `overlay/api/migrations/versions/2025_07_02_2332-1c9ba48be8e4_add_uuidv7_function_in_sql.py`
  (`1c9ba48be8e4`) to use PostgreSQL 18's native `uuidv7`
  function instead of creating an ambiguous public overload, while preserving
  the upstream boundary helper and schema-safe downgrade behavior
- Added the MIT-licensed `setup_model_provider.py` to `overlay/scripts/` for
  automated model provisioning
- Added Keycloak sign-in UI changes to the Dify-derived
  `overlay/web/app/signin/normal-form.tsx`,
  `overlay/web/app/signin/components/sso-redirect.tsx`,
  `overlay/web/app/signin/components/social-auth.tsx`, and
  `overlay/web/i18n/en-US/login.json` files;
  the web image is built from the pristine `langgenius/dify` 1.15.0 source tarball
  with this overlay applied before compilation
- Bundled unmodified `langgenius/openai_api_compatible:0.0.56` plugin package in
  `overlay/plugins/` (Apache License 2.0, Copyright 2025 LangGenius, Inc.),
  sourced from marketplace.dify.ai,
  sha256: 859e3d9496446e4dff192ca064012d5e34a76eb19eddba2d3fd9c40462991a22

See `THIRD_PARTY_NOTICES.md` for exact source revisions, checksums, and license
scope. Every Dify-derived Python and TypeScript file above carries a prominent
modification comment. Strict JSON has no comment syntax, so `login.json` carries
the equivalent `_licenseNotice` metadata entry. No Dify Enterprise Edition code
is included, copied, or derived from.
