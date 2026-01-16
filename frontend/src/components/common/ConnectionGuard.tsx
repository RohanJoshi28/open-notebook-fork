'use client'

import { useEffect, useState, useCallback } from 'react'
import { usePathname } from 'next/navigation'
import { ConnectionError } from '@/lib/types/config'
import { ConnectionErrorOverlay } from '@/components/errors/ConnectionErrorOverlay'
import { getConfig, resetConfig } from '@/lib/config'

interface ConnectionGuardProps {
  children: React.ReactNode
}

export function ConnectionGuard({ children }: ConnectionGuardProps) {
  const pathname = usePathname()
  const skipCheck = pathname?.startsWith('/auth') ?? false

  const [error, setError] = useState<ConnectionError | null>(null)
  const [isChecking, setIsChecking] = useState(true)

  const checkConnection = useCallback(async () => {
    setIsChecking(true)
    setError(null)

    // Reset config cache to force a fresh fetch
    resetConfig()

    try {
      await getConfig()

      // If we got here, connection is good
      setError(null)
      setIsChecking(false)
    } catch (err) {
      // API is unreachable
      const errorMessage =
        err instanceof Error ? err.message : 'Unknown error occurred'
      const attemptedUrl =
        typeof window !== 'undefined'
          ? `${window.location.origin}/api/config`
          : undefined

      setError({
        type: 'api-unreachable',
        details: {
          message: 'The Open Notebook API server could not be reached',
          technicalMessage: errorMessage,
          stack: err instanceof Error ? err.stack : undefined,
          attemptedUrl,
        },
      })
      setIsChecking(false)
    }
  }, [])

  // Check connection on mount
  useEffect(() => {
    if (skipCheck) {
      setIsChecking(false)
      setError(null)
      return
    }
    checkConnection()
  }, [checkConnection, skipCheck])

  // Add keyboard shortcut for retry (R key)
  useEffect(() => {
    if (skipCheck) return
    const handleKeyPress = (e: KeyboardEvent) => {
      if (error && (e.key === 'r' || e.key === 'R')) {
        e.preventDefault()
        checkConnection()
      }
    }

    window.addEventListener('keydown', handleKeyPress)
    return () => window.removeEventListener('keydown', handleKeyPress)
  }, [error, checkConnection, skipCheck])

  // Show overlay if there's an error
  if (error) {
    return <ConnectionErrorOverlay error={error} onRetry={checkConnection} />
  }

  // Never block auth routes
  if (skipCheck) {
    return <>{children}</>
  }

  // Show a lightweight splash while checking to avoid a blank screen
  if (isChecking) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background text-muted-foreground">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 rounded-full border-2 border-muted-foreground/40 border-t-primary animate-spin" />
          <div className="text-sm font-medium">Connecting to API…</div>
        </div>
      </div>
    )
  }

  // Render children if connection is good
  return <>{children}</>
}
