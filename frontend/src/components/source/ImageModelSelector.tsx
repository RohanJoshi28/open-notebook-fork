'use client'

import { useEffect, useMemo } from 'react'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useModels } from '@/lib/hooks/use-models'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import Link from 'next/link'

interface ImageModelSelectorProps {
  value?: string
  onChange: (value: string) => void
  disabled?: boolean
}

export function ImageModelSelector({ value, onChange, disabled }: ImageModelSelectorProps) {
  const { data: models, isLoading } = useModels()

  const imageModels = useMemo(
    () => (models ?? []).filter((model) => model.type === 'image'),
    [models]
  )

  useEffect(() => {
    if (!value && imageModels.length > 0) {
      onChange(imageModels[0].id)
    }
  }, [imageModels, onChange, value])

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <LoadingSpinner size="sm" />
        Loading image models...
      </div>
    )
  }

  if (imageModels.length === 0) {
    return (
      <Link
        href="/models"
        className="text-xs text-primary hover:underline"
      >
        Add an image model →
      </Link>
    )
  }

  return (
    <Select value={value} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger className="min-w-[200px] h-8">
        <SelectValue placeholder="Select image model" />
      </SelectTrigger>
      <SelectContent>
        {imageModels.map((model) => (
          <SelectItem key={model.id} value={model.id}>
            <div className="flex items-center justify-between gap-2">
              <span>{model.name}</span>
              <span className="text-xs text-muted-foreground capitalize">{model.provider}</span>
            </div>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
