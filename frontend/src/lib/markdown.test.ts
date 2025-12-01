import { describe, it, expect } from 'vitest'

import { transformImageUri } from './markdown'

describe('transformImageUri', () => {
  it('allows data URIs', () => {
    const uri = 'data:image/png;base64,AAA='
    expect(transformImageUri(uri)).toBe(uri)
  })

  it('allows #ref- links', () => {
    const uri = '#ref-source-source:abc'
    expect(transformImageUri(uri)).toBe(uri)
  })

  it('sanitizes invalid urls', () => {
    expect(transformImageUri('javascript:alert(1)')).toBe('')
  })
})
