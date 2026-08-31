/*
 * Modified by Neurwerk, 2025-2026: add the Keycloak SSO redirect component.
 * This Dify-derived file remains under the Dify Open Source License, based on
 * Apache License 2.0 with additional conditions. See LICENSES/Dify-LICENSE and
 * NOTICE-CHANGES.md.
 */
'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { API_PREFIX } from '@/config'
import { useLocale } from '@/context/i18n'
import { useSearchParams } from '@/next/navigation'
import { getPurifyHref } from '@/utils'
import { getBrowserTimezone } from '@/utils/timezone'

export default function SsoRedirect() {
  const { t } = useTranslation()
  const locale = useLocale()
  const searchParams = useSearchParams()
  const [countdown, setCountdown] = useState(3)

  const oauthUrl = useMemo(() => {
    const base = getPurifyHref(`${API_PREFIX}/oauth/login/keycloak`)
    const params = new URLSearchParams(searchParams.toString())
    const timezone = getBrowserTimezone()
    if (timezone)
      params.set('timezone', timezone)
    params.set('language', locale)
    const query = params.toString()
    return query ? `${base}?${query}` : base
  }, [locale, searchParams])

  const loginFormUrl = useMemo(() => {
    const params = new URLSearchParams(searchParams.toString())
    params.set('method', 'form')
    return `?${params.toString()}`
  }, [searchParams])

  useEffect(() => {
    if (countdown <= 0) {
      window.location.href = oauthUrl
      return
    }
    const timer = setTimeout(() => setCountdown(countdown - 1), 1000)
    return () => clearTimeout(timer)
  }, [countdown, oauthUrl])

  return (
    <div className="flex flex-col items-center justify-center gap-4 p-8">
      <p className="text-sm text-gray-500">
        {t('redirectingToSSO', { ns: 'login', seconds: countdown })}
      </p>
      <a
        href={oauthUrl}
        className="text-sm text-blue-600 hover:text-blue-800 underline"
      >
        {t('redirectNow', { ns: 'login' })}
      </a>
      <a
        href={loginFormUrl}
        className="text-sm text-gray-400 hover:text-gray-600"
      >
        {t('useLoginForm', { ns: 'login' })}
      </a>
    </div>
  )
}
