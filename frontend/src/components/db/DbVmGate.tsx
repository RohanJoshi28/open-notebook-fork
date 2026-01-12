'use client'

import { createContext, useContext, useEffect, useMemo, useState, useCallback } from 'react'
import { Power, RefreshCcw, Server, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useDbVmStatus, useStartDbVm, useStopDbVm } from '@/lib/hooks/use-db-vm'

const STORAGE_KEY = 'db-vm-start-ts'

type DbVmContextValue = {
  status: string
  rawStatus?: string
  isStarting: boolean
  isStopping: boolean
  isSuspending: boolean
  progress: number
  estimatedSeconds: number
  start: () => Promise<void>
  stop: () => Promise<void>
  refetch: () => Promise<unknown>
}

const DbVmContext = createContext<DbVmContextValue | null>(null)

export function useDbVmControl() {
  const ctx = useContext(DbVmContext)
  if (!ctx) {
    throw new Error('useDbVmControl must be used within DbVmGate')
  }
  return ctx
}

interface DbVmGateProps {
  children: React.ReactNode
}

export function DbVmGate({ children }: DbVmGateProps) {
  // Allow disabling the gate for local/dev by env or hostname
  const isLocalhost =
    typeof window !== 'undefined' &&
    (window.location.hostname === 'localhost' ||
      window.location.hostname === '127.0.0.1')
  const gateDisabled =
    (process.env.NEXT_PUBLIC_DISABLE_DB_VM_GATE === 'true' || isLocalhost) &&
    process.env.NEXT_PUBLIC_FORCE_DB_VM_GATE !== 'true'

  const { data, isLoading, refetch } = useDbVmStatus()
  const startMutation = useStartDbVm()
  const stopMutation = useStopDbVm()

  const [isStarting, setIsStarting] = useState(false)
  const [isStopping, setIsStopping] = useState(false)
  const [isSuspendingLocal, setIsSuspendingLocal] = useState(false)
  const [progress, setProgress] = useState(0)
  const [startTimestamp, setStartTimestamp] = useState<number | null>(null)
  const [startError, setStartError] = useState<string | null>(null)

  const status = data?.status ?? 'unknown'
  const rawStatus = data?.rawStatus
  const estimatedSeconds = data?.config?.estimatedStartSeconds ?? 90
  const isSuspending = status === 'suspending' || isSuspendingLocal
  const isStartingFromServer = status === 'starting'

  // Progress animation for the 90s bar
  useEffect(() => {
    if (!isStarting || !startTimestamp) return

    const id = setInterval(() => {
      const elapsedMs = Date.now() - startTimestamp
      const pct = Math.min(99, (elapsedMs / (estimatedSeconds * 1000)) * 100)
      setProgress(pct)
    }, 400)

    return () => clearInterval(id)
  }, [estimatedSeconds, isStarting, startTimestamp])

  // Auto-complete progress when VM flips to running
  useEffect(() => {
    if (status === 'running') {
      setProgress(100)
      setIsStarting(false)
      setIsStopping(false)
      setIsSuspendingLocal(false)
      localStorage.removeItem(STORAGE_KEY)
    }
  }, [status])

  // Persist start timestamp so refresh keeps the bar
  useEffect(() => {
    if (startTimestamp && isStarting) {
      localStorage.setItem(STORAGE_KEY, startTimestamp.toString())
    }
  }, [startTimestamp, isStarting])

  // Restore start state on mount / when server says starting
  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) {
      const ts = Number(stored)
      if (!Number.isNaN(ts)) {
        setStartTimestamp(ts)
        setIsStarting(true)
      }
    } else if (isStartingFromServer && !isStarting) {
      const now = Date.now()
      setStartTimestamp(now)
      setIsStarting(true)
      localStorage.setItem(STORAGE_KEY, now.toString())
    }
  }, [isStartingFromServer, isStarting])

  const start = useCallback(async () => {
    setStartError(null)
    setIsStarting(true)
    setProgress(0)
    setStartTimestamp(Date.now())
    try {
      await startMutation.mutateAsync()
      // status query is already polling when not running; no extra action needed
    } catch (err) {
      console.error('Failed to start VM', err)
      setStartError('Unable to start the database VM. Please try again.')
      setIsStarting(false)
    }
  }, [startMutation])

  const stop = useCallback(async () => {
    setIsStopping(true)
    setIsSuspendingLocal(true) // optimistic UI while backend transitions
    try {
      await stopMutation.mutateAsync()
      await refetch()
    } catch (err) {
      console.error('Failed to stop VM', err)
      setIsStopping(false)
      setIsSuspendingLocal(false)
    }
  }, [stopMutation, refetch])

  // Reset local suspending flag once backend reports non-running state
  useEffect(() => {
    if (status === 'suspended' || status === 'stopped') {
      setIsSuspendingLocal(false)
      setIsStopping(false)
    }
  }, [status])

  const contextValue: DbVmContextValue = useMemo(
    () => ({
      status,
      rawStatus,
      isStarting: isStarting || isStartingFromServer,
      isStopping,
      isSuspending,
      progress,
      estimatedSeconds,
      start,
      stop,
      refetch,
    }),
    [status, rawStatus, isStarting, isStartingFromServer, isStopping, isSuspending, progress, estimatedSeconds, start, stop, refetch]
  )

  const shouldGate =
    status !== 'running' || isStarting || isStopping || isLoading

  const startDisabled = isStarting || isStartingFromServer || isStopping || isSuspending

  return (
    <DbVmContext.Provider value={contextValue}>
      {gateDisabled ? (
        children
      ) : shouldGate ? (
        <div className="min-h-screen flex items-center justify-center bg-gradient-to-b from-slate-950 via-slate-900 to-slate-950 text-slate-100 transition-opacity duration-300">
          <div className="w-full max-w-xl rounded-2xl border border-white/10 bg-white/5 p-8 shadow-2xl backdrop-blur">
            <div className="flex items-center gap-3 mb-4">
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-white/10">
                {status === 'running' ? (
                  <Server className="h-6 w-6 text-emerald-300" />
                ) : isStarting ? (
                  <Loader2 className="h-6 w-6 animate-spin text-amber-200" />
                ) : isSuspending || isStopping ? (
                  <Loader2 className="h-6 w-6 animate-spin text-rose-200" />
                ) : (
                  <Power className="h-6 w-6 text-rose-200" />
                )}
              </div>
              <div>
                <p className="text-sm uppercase tracking-[0.2em] text-white/60">
                  Database VM
                </p>
                <p className="text-xl font-semibold">
                  {status === 'running'
                    ? 'Online'
                    : isSuspending || isStopping
                      ? 'Suspending...'
                      : (isStarting || isStartingFromServer)
                        ? 'Starting...'
                        : 'Offline'}
                </p>
              </div>
            </div>

            <p className="text-sm text-white/70 mb-6">
              {isStarting
                ? 'Warming up the database server. This can take a short while.'
                : isSuspending || isStopping
                  ? 'The server is suspending; please wait until it finishes.'
                  : 'The database VM is currently off. Start it to enter Open Notebook.'}
            </p>

            <div className="space-y-4">
              <Button
                size="lg"
                className={cn(
                  'w-full text-base font-semibold h-11',
                  isStarting
                    ? 'bg-amber-400 text-slate-900 hover:bg-amber-300'
                    : 'bg-emerald-400 text-slate-900 hover:bg-emerald-300'
                )}
                onClick={start}
                disabled={startDisabled}
              >
                {isStarting ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Starting server…
                  </>
                ) : isSuspending || isStopping ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Suspending…
                  </>
                ) : (
                  <>
                    <Power className="mr-2 h-4 w-4" />
                    Start the server
                  </>
                )}
              </Button>

              <div className="h-3 rounded-full bg-white/10 overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-amber-300 via-emerald-300 to-emerald-500 transition-all duration-300"
                  style={{ width: `${progress}%` }}
                />
              </div>

              {startError && (
                <p className="text-xs text-rose-200">{startError}</p>
              )}

              {status !== 'running' && (
                <div className="flex items-center gap-2 text-xs text-white/60">
                  <RefreshCcw className="h-3.5 w-3.5" />
                  <span>
                    We’ll auto-refresh as soon as the VM reports running and
                    drop you into the app.
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
      ) : (
        children
      )}
    </DbVmContext.Provider>
  )
}
