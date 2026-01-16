'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/lib/hooks/use-auth'
import { useAuthStore } from '@/lib/stores/auth-store'
import { getConfig } from '@/lib/config'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { AlertCircle } from 'lucide-react'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'

export function LoginForm() {
  const [googleClientId, setGoogleClientId] = useState<string | null>(null)
  const { isLoading, error } = useAuth()
  const { authRequired, checkAuthRequired, hasHydrated, isAuthenticated } = useAuthStore()
  const [isCheckingAuth, setIsCheckingAuth] = useState(true)
  const [configInfo, setConfigInfo] = useState<{ apiUrl: string; version: string; buildTime: string } | null>(null)
  const router = useRouter()

  // Load config info for debugging
  useEffect(() => {
    getConfig().then(cfg => {
      setConfigInfo({
        apiUrl: cfg.apiUrl,
        version: cfg.version,
        buildTime: cfg.buildTime,
      })
      // Always cache client ID (even if authRequired is already known)
      setGoogleClientId(cfg.googleClientId || null)
    }).catch(err => {
      console.error('Failed to load config:', err)
    })
  }, [])

  // Check if authentication is required on mount
  useEffect(() => {
    if (!hasHydrated) {
      return
    }

    const checkAuth = async () => {
      try {
        const required = await checkAuthRequired()
        const cfg = await getConfig()
        setGoogleClientId(cfg.googleClientId || null)

        // If auth is not required, redirect to notebooks
        if (!required) {
          router.push('/notebooks')
        }
      } catch (error) {
        console.error('Error checking auth requirement:', error)
        // On error, assume auth is required to be safe
      } finally {
        setIsCheckingAuth(false)
      }
    }

    // If we already know auth status, use it
    if (authRequired !== null) {
      if (!authRequired && isAuthenticated) {
        router.push('/notebooks')
      } else {
        setIsCheckingAuth(false)
      }
    } else {
      void checkAuth()
    }
  }, [hasHydrated, authRequired, checkAuthRequired, router, isAuthenticated])

  // Show loading while checking if auth is required
  if (!hasHydrated || isCheckingAuth) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <LoadingSpinner />
      </div>
    )
  }

  // If we still don't know if auth is required (connection error), show error
  if (authRequired === null) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background p-4">
        <Card className="w-full max-w-md">
          <CardHeader className="text-center">
            <CardTitle>Connection Error</CardTitle>
            <CardDescription>
              Unable to connect to the API server
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              <div className="flex items-start gap-2 text-red-600 text-sm">
                <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                <div className="flex-1">
                  {error || 'Unable to connect to server. Please check if the API is running.'}
                </div>
              </div>

              {configInfo && (
                <div className="space-y-2 text-xs text-muted-foreground border-t pt-3">
                  <div className="font-medium">Diagnostic Information:</div>
                  <div className="space-y-1 font-mono">
                    <div>Version: {configInfo.version}</div>
                    <div>Built: {new Date(configInfo.buildTime).toLocaleString()}</div>
                    <div className="break-all">API URL: {configInfo.apiUrl}</div>
                    <div className="break-all">Frontend: {typeof window !== 'undefined' ? window.location.href : 'N/A'}</div>
                  </div>
                  <div className="text-xs pt-2">
                    Check browser console for detailed logs (look for 🔧 [Config] messages)
                  </div>
                </div>
              )}

              <Button
                onClick={() => window.location.reload()}
                className="w-full"
              >
                Retry Connection
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  const handleGoogleClick = async () => {
    if (!googleClientId || typeof window === 'undefined') return
    const origin = window.location.origin
    const redirectUri = `${origin}/auth/callback`
    const scope = [
      'openid',
      'email',
      'profile',
      'https://www.googleapis.com/auth/drive.readonly',
      'https://www.googleapis.com/auth/drive.metadata.readonly',
    ].join(' ')
    const params = new URLSearchParams({
      client_id: googleClientId,
      redirect_uri: redirectUri,
      response_type: 'code',
      scope,
      access_type: 'offline',
      prompt: 'consent select_account',
      hd: 'force10partners.com',
      include_granted_scopes: 'true',
    })
    window.location.href = `https://accounts.google.com/o/oauth2/v2/auth?${params.toString()}`
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <CardTitle>Open Notebook</CardTitle>
          <CardDescription>
            Sign in with your @force10partners.com Google account
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {error && (
            <div className="flex items-center gap-2 text-red-600 text-sm">
              <AlertCircle className="h-4 w-4" />
              {error}
            </div>
          )}

          <Button
            type="button"
            className="w-full"
            disabled={isLoading || !googleClientId}
            onClick={handleGoogleClick}
          >
            {isLoading ? 'Signing in...' : 'Continue with Google'}
          </Button>

          {process.env.NODE_ENV !== 'production' && (
            <div className="text-[11px] text-muted-foreground space-y-1 border-t pt-2">
              <div>Google Client ID: {googleClientId || 'null'}</div>
            </div>
          )}

          {configInfo && (
            <div className="text-xs text-center text-muted-foreground pt-2 border-t">
              <div>Version {configInfo.version}</div>
              <div className="font-mono text-[10px]">{configInfo.apiUrl}</div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
