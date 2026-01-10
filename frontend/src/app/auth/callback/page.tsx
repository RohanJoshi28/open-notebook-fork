"use client"

import { useEffect, useState } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import { useAuth } from '@/lib/hooks/use-auth'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'

export default function GoogleCallbackPage() {
  const params = useSearchParams()
  const router = useRouter()
  const { loginWithCode, error } = useAuth()
  const [status, setStatus] = useState<'processing' | 'success' | 'error'>('processing')

  useEffect(() => {
    const code = params.get('code')
    const err = params.get('error')
    if (err) {
      setStatus('error')
      return
    }
    if (!code) {
      setStatus('error')
      return
    }
    const doExchange = async () => {
      const redirectUri = `${window.location.origin}/auth/callback`
      const ok = await loginWithCode(code, redirectUri)
      if (ok) {
        setStatus('success')
        router.replace('/notebooks')
      } else {
        setStatus('error')
      }
    }
    void doExchange()
  }, [params, loginWithCode, router])

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Signing you in…</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-muted-foreground">
          {status === 'processing' && (
            <div className="flex items-center gap-2">
              <LoadingSpinner />
              <span>Exchanging Google code…</span>
            </div>
          )}
          {status === 'success' && <div>Success. Redirecting…</div>}
          {status === 'error' && <div>Login failed. Please go back and try again.</div>}
          {error && <div className="text-red-600 text-xs">{error}</div>}
        </CardContent>
      </Card>
    </div>
  )
}
