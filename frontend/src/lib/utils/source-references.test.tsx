import { describe, it, expect, vi } from 'vitest'

import {
  createCompactReferenceLinkComponent,
  createReferenceLinkComponent,
} from './source-references'

const mockChildren = '1'

describe('reference link components', () => {
  it('renders anchor for source references in compact links', () => {
    const LinkComponent = createCompactReferenceLinkComponent(vi.fn())
    const element = LinkComponent({
      href: '#ref-source-source:abc123',
      children: mockChildren,
    })

    expect(element.type).toBe('a')
    expect(element.props.href).toBe('/sources/source%3Aabc123')
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
})
