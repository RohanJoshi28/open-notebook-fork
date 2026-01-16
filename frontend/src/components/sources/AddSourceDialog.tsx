'use client'

import { useState, useRef, useEffect, useMemo, useCallback } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { LoaderIcon, CheckCircleIcon, XCircleIcon } from 'lucide-react'
import { toast } from 'sonner'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { WizardContainer, WizardStep } from '@/components/ui/wizard-container'
import { SourceTypeStep, parseAndValidateUrls } from './steps/SourceTypeStep'
import { NotebooksStep } from './steps/NotebooksStep'
import { ProcessingStep } from './steps/ProcessingStep'
import { useNotebooks } from '@/lib/hooks/use-notebooks'
import { useTransformations } from '@/lib/hooks/use-transformations'
import { useCreateSource } from '@/lib/hooks/use-sources'
import { useSettings } from '@/lib/hooks/use-settings'
import { CreateSourceRequest } from '@/lib/types/api'
import apiClient from '@/lib/api/client'
import { driveApi } from '@/lib/api/drive'
import { DriveResolvedItem } from '@/lib/types/drive'
import { isDriveUrl } from '@/lib/utils/google-drive'

const createSourceSchema = z.object({
  type: z.enum(['link', 'upload', 'text']),
  title: z.string().optional(),
  url: z.string().optional(),
  content: z.string().optional(),
  file: z.any().optional(),
  notebooks: z.array(z.string()).optional(),
  transformations: z.array(z.string()).optional(),
  embed: z.boolean(),
  async_processing: z.boolean(),
}).refine((data) => {
  if (data.type === 'link') {
    return !!data.url && data.url.trim() !== ''
  }
  if (data.type === 'text') {
    return !!data.content && data.content.trim() !== ''
  }
  if (data.type === 'upload') {
    if (data.file instanceof FileList) {
      return data.file.length > 0
    }
    return !!data.file
  }
  return true
}, {
  message: 'Please provide the required content for the selected source type',
  path: ['type'],
}).refine((data) => {
  // Make title mandatory for text sources
  if (data.type === 'text') {
    return !!data.title && data.title.trim() !== ''
  }
  return true
}, {
  message: 'Title is required for text sources',
  path: ['title'],
})

type CreateSourceFormData = z.infer<typeof createSourceSchema>

interface AddSourceDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  defaultNotebookId?: string
}

const WIZARD_STEPS: readonly WizardStep[] = [
  { number: 1, title: 'Source & Content', description: 'Choose type and add content' },
  { number: 2, title: 'Organization', description: 'Select notebooks' },
  { number: 3, title: 'Processing', description: 'Choose transformations and options' },
]

interface ProcessingState {
  message: string
  progress?: number
}

interface BatchProgress {
  total: number
  completed: number
  failed: number
  currentItem?: string
}

type SourceItem =
  | { kind: 'url'; url: string }
  | { kind: 'drive'; url: string; drive: DriveResolvedItem }
  | { kind: 'file'; file: File }
  | { kind: 'text' }

export function AddSourceDialog({ 
  open, 
  onOpenChange, 
  defaultNotebookId 
}: AddSourceDialogProps) {
  const DEBUG_LOGS =
    process.env.NEXT_PUBLIC_DEBUG_LOGS === '1' || process.env.NODE_ENV === 'development'
  const log = useCallback((...args: unknown[]) => {
    if (DEBUG_LOGS) console.debug('[AddSourceDialog]', ...args)
  }, [DEBUG_LOGS])

  // Simplified state management
  const [currentStep, setCurrentStep] = useState(1)
  const [processing, setProcessing] = useState(false)
  const [processingStatus, setProcessingStatus] = useState<ProcessingState | null>(null)
  const [selectedNotebooks, setSelectedNotebooks] = useState<string[]>(
    defaultNotebookId ? [defaultNotebookId] : []
  )
  const [selectedTransformations, setSelectedTransformations] = useState<string[]>([])

  // Batch-specific state
  const [urlValidationErrors, setUrlValidationErrors] = useState<{ url: string; line: number }[]>([])
  const [batchProgress, setBatchProgress] = useState<BatchProgress | null>(null)

  // Cleanup timeouts to prevent memory leaks
  const timeoutRef = useRef<NodeJS.Timeout | null>(null)

  // API hooks
  const createSource = useCreateSource()
  const { data: notebooks = [], isLoading: notebooksLoading } = useNotebooks()
  const { data: transformations = [], isLoading: transformationsLoading } = useTransformations()
  const { data: settings, isLoading: settingsLoading } = useSettings()
  const dataLoaded = !transformationsLoading && !settingsLoading && !!settings
  const defaultsInitialized = useRef(false)

  // Form setup
  const {
    register,
    handleSubmit,
    control,
    watch,
    formState: { errors },
    reset,
  } = useForm<CreateSourceFormData>({
    resolver: zodResolver(createSourceSchema),
    defaultValues: {
      notebooks: defaultNotebookId ? [defaultNotebookId] : [],
      embed: false,
      async_processing: true,
      transformations: [],
    },
  })

  // Initialize form values when settings and transformations are loaded
  useEffect(() => {
    if (!dataLoaded || defaultsInitialized.current) return

    const defaultTransformations = Array.from(
      new Set(
        transformations
          .filter(t => t.apply_default)
          .map(t => t.id)
      )
    )

    setSelectedTransformations(defaultTransformations)

    // Reset form with proper embed value based on settings
    const embedValue = settings?.default_embedding_option === 'always' ||
                       (settings?.default_embedding_option === 'ask')

    reset({
      notebooks: defaultNotebookId ? [defaultNotebookId] : [],
      embed: embedValue,
      async_processing: true,
      transformations: [],
    })

    defaultsInitialized.current = true
    log('Defaults initialized', { defaultTransformations, embedValue })
  }, [dataLoaded, transformations, defaultNotebookId, reset, settings, log])

  // Cleanup effect
  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current)
      }
    }
  }, [])

  const selectedType = watch('type')
  const watchedUrl = watch('url')
  const watchedContent = watch('content')
  const watchedFile = watch('file')
  const watchedTitle = watch('title')

  // Batch mode detection
  const { isBatchMode, parsedUrls, parsedFiles } = useMemo(() => {
    let urlCount = 0
    let fileCount = 0
    let parsedUrls: string[] = []
    let parsedFiles: File[] = []

    if (selectedType === 'link' && watchedUrl) {
      const { valid } = parseAndValidateUrls(watchedUrl)
      parsedUrls = valid
      urlCount = valid.length
    }

    if (selectedType === 'upload' && watchedFile) {
      const fileList = watchedFile as FileList
      if (fileList?.length) {
        parsedFiles = Array.from(fileList)
        fileCount = parsedFiles.length
      }
    }

    const isBatchMode = urlCount > 1 || fileCount > 1

    return { isBatchMode, parsedUrls, parsedFiles }
  }, [selectedType, watchedUrl, watchedFile])

  // Step validation - now reactive with watched values
  const isStepValid = (step: number): boolean => {
    switch (step) {
      case 1:
        if (!selectedType) return false
        // Check for URL validation errors
        if (urlValidationErrors.length > 0) return false

        if (selectedType === 'link') {
          // In batch mode, check that we have at least one valid URL
          if (isBatchMode) {
            return parsedUrls.length > 0
          }
          return !!watchedUrl && watchedUrl.trim() !== ''
        }
        if (selectedType === 'text') {
          return !!watchedContent && watchedContent.trim() !== '' &&
                 !!watchedTitle && watchedTitle.trim() !== ''
        }
        if (selectedType === 'upload') {
          if (watchedFile instanceof FileList) {
            return watchedFile.length > 0
          }
          return !!watchedFile
        }
        return true
      case 2:
      case 3:
        return true
      default:
        return false
    }
  }

  // Navigation
  const handleNextStep = (e?: React.MouseEvent) => {
    e?.preventDefault()
    e?.stopPropagation()

    // Validate URLs when leaving step 1 in link mode
    if (currentStep === 1 && selectedType === 'link' && watchedUrl) {
      const { invalid } = parseAndValidateUrls(watchedUrl)
      if (invalid.length > 0) {
        setUrlValidationErrors(invalid)
        return
      }
      setUrlValidationErrors([])
    }

    if (currentStep < 3 && isStepValid(currentStep)) {
      setCurrentStep(currentStep + 1)
    }
  }

  // Clear URL validation errors when user edits
  const handleClearUrlErrors = () => {
    setUrlValidationErrors([])
  }

  const handlePrevStep = (e?: React.MouseEvent) => {
    e?.preventDefault()
    e?.stopPropagation()
    if (currentStep > 1) {
      setCurrentStep(currentStep - 1)
    }
  }

  const handleStepClick = (step: number) => {
    if (step <= currentStep || (step === currentStep + 1 && isStepValid(currentStep))) {
      setCurrentStep(step)
    }
  }

  // Selection handlers
  const handleNotebookToggle = (notebookId: string) => {
    const updated = selectedNotebooks.includes(notebookId)
      ? selectedNotebooks.filter(id => id !== notebookId)
      : [...selectedNotebooks, notebookId]
    setSelectedNotebooks(updated)
  }

  const handleTransformationToggle = (transformationId: string) => {
    const updated = selectedTransformations.includes(transformationId)
      ? selectedTransformations.filter(id => id !== transformationId)
      : [...selectedTransformations, transformationId]
    setSelectedTransformations(updated)
  }

  const buildCreateRequest = (item: SourceItem, data: CreateSourceFormData): CreateSourceRequest & { file?: File } => {
    const base: CreateSourceRequest & { file?: File } = {
      type: item.kind === 'file' ? 'upload' : data.type,
      notebooks: selectedNotebooks,
      transformations: selectedTransformations,
      embed: data.embed,
      delete_source: false,
      async_processing: true,
      title: data.title,
    }

    switch (item.kind) {
      case 'url':
        base.type = 'link'
        base.url = item.url
        break
      case 'drive':
        base.type = 'link'
        base.url = item.url
        base.drive_file_id = item.drive.id
        base.drive_resource_key = item.drive.resource_key
        base.drive_file_name = item.drive.name
        base.drive_mime_type = item.drive.mime_type
        base.drive_export_mime_type = item.drive.export_mime_type
        if (!base.title) base.title = item.drive.name
        break
      case 'file':
        base.type = 'upload'
        base.file = item.file
        break
      case 'text':
        base.type = 'text'
        base.content = data.content
        base.title = data.title
        break
    }
    return base
  }

  const resolveDriveItems = async (url: string): Promise<DriveResolvedItem[]> => {
    setProcessingStatus({ message: 'Resolving Google Drive link...' })
    const res = await driveApi.resolve(url, true)
    if (!res.items || res.items.length === 0) {
      throw new Error('No files found in Drive link')
    }
    return res.items
  }

  const buildItems = async (data: CreateSourceFormData): Promise<SourceItem[]> => {
    if (data.type === 'text') return [{ kind: 'text' }]
    if (data.type === 'upload') {
      const files = parsedFiles.length > 0 ? parsedFiles : (data.file instanceof FileList ? Array.from(data.file) : [])
      return files.map(file => ({ kind: 'file', file }))
    }
    if (data.type === 'link') {
      const urls = parsedUrls
      const items: SourceItem[] = []
      for (const url of urls) {
        if (isDriveUrl(url)) {
          try {
            const resolved = await resolveDriveItems(url)
            resolved.forEach(item => items.push({ kind: 'drive', url, drive: item }))
          } catch (err) {
            console.error('Drive resolve failed', err)
            throw err
          }
        } else {
          items.push({ kind: 'url', url })
        }
      }
      return items
    }
    return []
  }

  // Single source submission (returns source id)
  const submitSingleSource = async (item: SourceItem, data: CreateSourceFormData): Promise<string> => {
    const createRequest = buildCreateRequest(item, data)
    log('Submitting source', { createRequest })
    const res = await createSource.mutateAsync(createRequest)
    const sid = res?.id || ''
    log('Submit response', res)
    return sid
  }

  // Batch submission
  const submitBatch = async (items: SourceItem[], data: CreateSourceFormData): Promise<{ success: number; failed: number }> => {
    const results = { success: 0, failed: 0 }
    setBatchProgress({
      total: items.length,
      completed: 0,
      failed: 0,
    })

    for (let i = 0; i < items.length; i++) {
      const item = items[i]
      const itemLabel =
        item.kind === 'file'
          ? item.file.name
          : item.kind === 'drive'
            ? item.drive.name
            : item.kind === 'url'
              ? item.url.substring(0, 50) + '...'
              : 'text'

      setBatchProgress(prev => prev ? { ...prev, currentItem: itemLabel } : null)

      try {
        const createRequest = buildCreateRequest(item, data)
        await createSource.mutateAsync(createRequest)
        results.success++
      } catch (error) {
        console.error(`Error creating source for ${itemLabel}:`, error)
        results.failed++
      }

      setBatchProgress(prev => prev ? {
        ...prev,
        completed: results.success,
        failed: results.failed,
      } : null)
    }

    return results
  }

  // Form submission
  const onSubmit = async (data: CreateSourceFormData) => {
    try {
      if (!dataLoaded || !defaultsInitialized.current) {
        return
      }
      setProcessing(true)
      setProcessingStatus({ message: 'Preparing items...' })
      const items = await buildItems(data)
      if (!items || items.length === 0) {
        throw new Error('No items to import')
      }

      const batchMode = items.length > 1

      if (batchMode) {
        setProcessingStatus({ message: `Processing ${items.length} sources...` })
        const results = await submitBatch(items, data)

        if (results.failed === 0) {
          toast.success(`${results.success} source${results.success !== 1 ? 's' : ''} created successfully`)
        } else if (results.success === 0) {
          toast.error(`Failed to create all ${results.failed} sources`)
        } else {
          toast.warning(`${results.success} succeeded, ${results.failed} failed`)
        }

        handleClose()
      } else {
        const item = items[0]
        setProcessingStatus({ message: 'Submitting source for processing...' })
        const sourceId = await submitSingleSource(item, data)
        if (!sourceId) {
          throw new Error('Source id missing from response')
        }
        setProcessingStatus({ message: 'Processing source...' })

        const poll = async () => {
          const statusRes = await apiClient.get(`/sources/${encodeURIComponent(sourceId)}/status`)
          const statusJson = statusRes.data
          log('Status poll', statusJson)
          const state = statusJson.status || statusJson.processing_info?.status
          if (state === 'completed') return true
          if (state === 'failed') throw new Error(statusJson.processing_info?.error || 'Processing failed')
          return false
        }

        const start = Date.now()
        const timeoutMs = 120000
        while (true) {
          if (Date.now() - start > timeoutMs) {
            throw new Error('Processing timed out')
          }
          const done = await poll()
          if (done) break
          await new Promise(r => setTimeout(r, 1500))
        }

        toast.success('Source processed')
        handleClose()
      }
    } catch (error) {
      console.error('Error creating source:', error)
      const message = error instanceof Error ? error.message : 'Error creating source. Please try again.'
      setProcessingStatus({
        message,
      })
      timeoutRef.current = setTimeout(() => {
        setProcessing(false)
        setProcessingStatus(null)
        setBatchProgress(null)
      }, 3000)
    }
  }

  // Dialog management
  const handleClose = () => {
    // Clear any pending timeouts
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current)
      timeoutRef.current = null
    }

    reset()
    setCurrentStep(1)
    setProcessing(false)
    setProcessingStatus(null)
    setSelectedNotebooks(defaultNotebookId ? [defaultNotebookId] : [])
    setUrlValidationErrors([])
    setBatchProgress(null)

    // Reset to default transformations
    if (transformations.length > 0) {
      const defaultTransformations = Array.from(
        new Set(
          transformations
            .filter(t => t.apply_default)
            .map(t => t.id)
        )
      )
      setSelectedTransformations(defaultTransformations)
    } else {
      setSelectedTransformations([])
    }

    onOpenChange(false)
  }

  // Processing view
  if (processing) {
    const progressPercent = batchProgress
      ? Math.round(((batchProgress.completed + batchProgress.failed) / batchProgress.total) * 100)
      : undefined

    return (
      <Dialog open={open} onOpenChange={handleClose}>
        <DialogContent className="sm:max-w-[500px]" showCloseButton={true}>
          <DialogHeader>
            <DialogTitle>
              {batchProgress ? 'Processing Batch' : 'Processing Source'}
            </DialogTitle>
            <DialogDescription>
              {batchProgress
                ? `Processing ${batchProgress.total} sources. This may take a few moments.`
                : 'Your source is being processed. This may take a few moments.'
              }
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-4">
            <div className="flex items-center gap-3">
              <LoaderIcon className="h-5 w-5 animate-spin text-primary" />
              <span className="text-sm text-muted-foreground">
                {processingStatus?.message || 'Processing...'}
              </span>
            </div>

            {/* Batch progress */}
            {batchProgress && (
              <>
                <div className="w-full bg-muted rounded-full h-2">
                  <div
                    className="bg-primary h-2 rounded-full transition-all duration-300"
                    style={{ width: `${progressPercent}%` }}
                  />
                </div>

                <div className="flex items-center justify-between text-sm">
                  <div className="flex items-center gap-4">
                    <span className="flex items-center gap-1.5 text-green-600">
                      <CheckCircleIcon className="h-4 w-4" />
                      {batchProgress.completed} completed
                    </span>
                    {batchProgress.failed > 0 && (
                      <span className="flex items-center gap-1.5 text-destructive">
                        <XCircleIcon className="h-4 w-4" />
                        {batchProgress.failed} failed
                      </span>
                    )}
                  </div>
                  <span className="text-muted-foreground">
                    {batchProgress.completed + batchProgress.failed} / {batchProgress.total}
                  </span>
                </div>

                {batchProgress.currentItem && (
                  <p className="text-xs text-muted-foreground truncate">
                    Current: {batchProgress.currentItem}
                  </p>
                )}
              </>
            )}

            {/* Single source progress */}
            {!batchProgress && processingStatus?.progress && (
              <div className="w-full bg-muted rounded-full h-2">
                <div
                  className="bg-primary h-2 rounded-full transition-all duration-300"
                  style={{ width: `${processingStatus.progress}%` }}
                />
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    )
  }

  const currentStepValid = isStepValid(currentStep)

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-[700px] p-0">
        <DialogHeader className="px-6 pt-6 pb-0">
          <DialogTitle>Add New Source</DialogTitle>
          <DialogDescription>
            Add content from links, uploads, or text to your notebooks.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit(onSubmit)}>
          <WizardContainer
            currentStep={currentStep}
            steps={WIZARD_STEPS}
            onStepClick={handleStepClick}
            className="border-0"
          >
            {currentStep === 1 && (
              <SourceTypeStep
                // @ts-expect-error - Type inference issue with zod schema
                control={control}
                register={register}
                // @ts-expect-error - Type inference issue with zod schema
                errors={errors}
                urlValidationErrors={urlValidationErrors}
                onClearUrlErrors={handleClearUrlErrors}
              />
            )}
            
            {currentStep === 2 && (
              <NotebooksStep
                notebooks={notebooks}
                selectedNotebooks={selectedNotebooks}
                onToggleNotebook={handleNotebookToggle}
                loading={notebooksLoading}
              />
            )}
            
            {currentStep === 3 && (
            <ProcessingStep
              // @ts-expect-error - Type inference issue with zod schema
              control={control}
              transformations={transformations}
              selectedTransformations={selectedTransformations}
              onToggleTransformation={handleTransformationToggle}
              loading={transformationsLoading || settingsLoading}
              settings={settings}
            />
          )}
          </WizardContainer>

          {/* Navigation */}
          <div className="flex justify-between items-center px-6 py-4 border-t border-border bg-muted">
            <Button 
              type="button" 
              variant="outline" 
              onClick={handleClose}
            >
              Cancel
            </Button>

            <div className="flex gap-2">
              {currentStep > 1 && (
                <Button
                  type="button"
                  variant="outline"
                  onClick={handlePrevStep}
                >
                  Back
                </Button>
              )}

              {/* Show Next button on steps 1 and 2, styled as outline/secondary */}
              {currentStep < 3 && (
                <Button
                  type="button"
                  variant="outline"
                  onClick={(e) => handleNextStep(e)}
                  disabled={!currentStepValid || !dataLoaded}
                >
                  Next
                </Button>
              )}

              {/* Show Done button on all steps, styled as primary */}
              <Button
                type="submit"
                disabled={!currentStepValid || createSource.isPending || !dataLoaded || !defaultsInitialized.current}
                className="min-w-[120px]"
              >
                {createSource.isPending ? 'Creating...' : 'Done'}
              </Button>
            </div>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
