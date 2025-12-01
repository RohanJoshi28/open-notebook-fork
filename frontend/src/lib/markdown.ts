import type { ComponentProps } from 'react'
import { uriTransformer } from 'react-markdown/lib/uri-transformer.js'

import { cn } from '@/lib/utils'

/** Allow data URI images while still falling back to react-markdown's sanitizer. */
export function transformImageUri(uri?: string): string {
  if (!uri) return ''
  if (uri.startsWith('data:image/')) {
    return uri
  }
  return uriTransformer(uri)
}

export function MarkdownImage({ className, loading, ...props }: ComponentProps<'img'>) {
  return (
    <img
      loading={loading ?? 'lazy'}
      className={cn('rounded-lg border bg-muted/20 shadow max-w-full', className)}
      {...props}
    />
  )
}
