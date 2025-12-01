import { describe, it, expect, vi } from 'vitest'

import {
  createCompactReferenceLinkComponent,
  createReferenceLinkComponent,
  parseSourceReferences,
  convertReferencesToCompactMarkdown,
} from './source-references'

const mockChildren = '1'

describe('reference link components', () => {
  it('parses ids that include prefixes and hyphens', () => {
    const refs = parseSourceReferences('see [source:source:abc-123]')
    expect(refs[0]).toMatchObject({ type: 'source', id: 'source:abc-123' })
  })

  it('renders anchor for source references in compact links', () => {
    const LinkComponent = createCompactReferenceLinkComponent(vi.fn())
    const element = LinkComponent({
      href: '#ref-source-source:abc-123',
      children: mockChildren,
    })

    expect(element.type).toBe('a')
    expect(element.props.href).toBe('/sources/source%3Aabc-123')
  })

  it('renders anchor for source references in regular links', () => {
    const LinkComponent = createReferenceLinkComponent(vi.fn())
    const element = LinkComponent({
      href: '#ref-source-source:xyz789',
      children: mockChildren,
    })

    expect(element.type).toBe('a')
    expect(element.props.href).toBe('/sources/source%3Axyz789')
  })

  it('converts colon-prefixed references to numbered citations', () => {
    const markdown = convertReferencesToCompactMarkdown('See [source:source:abc-123].')
    expect(markdown).toContain('#ref-source-source:abc-123')
  })
})
