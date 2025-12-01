import type { ComponentProps } from 'react'
import * as React from 'react'

import { cn } from '@/lib/utils'

/** Allow data URI images while still falling back to a basic sanitizer. */
export function transformImageUri(uri?: string): string {
  if (!uri) return ''
  if (uri.startsWith('data:image/')) {
    return uri
  }
  try {
    const safeUrl = new URL(uri)
    return safeUrl.toString()
  } catch {
    return ''
  }
}

export function MarkdownImage({ className, ...props }: ComponentProps<'img'>) {
  return (
    <img
      loading="lazy"
      className={cn('rounded-lg border bg-muted/20 shadow max-w-full', className)}
      {...props}
    />
  )
}
