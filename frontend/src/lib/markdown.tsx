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

export function MarkdownImage({ className, alt, ...props }: ComponentProps<'img'>) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      loading="lazy"
      alt={alt ?? 'Generated image'}
      className={cn('rounded-lg border bg-muted/20 shadow max-w-full', className)}
      {...props}
    />
  )
}
