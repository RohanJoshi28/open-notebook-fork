import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'

import { AIMessageContent } from '../ChatPanel'

describe('AIMessageContent', () => {
  it('renders source links as anchors to /sources', async () => {
    const onReferenceClick = vi.fn()
    render(
      <AIMessageContent
        content={'See [source:source:abc-123].'}
        onReferenceClick={onReferenceClick}
      />
    )

    const links = await screen.findAllByRole('link')
    const sourceLink = links.find((link) => link.getAttribute('href') === '/sources/source%3Aabc-123')
    expect(sourceLink).toBeTruthy()
    await sourceLink!.click()
    expect(onReferenceClick).not.toHaveBeenCalled()
  })

  it('invokes callback for note references', async () => {
    const onReferenceClick = vi.fn()
    render(
      <AIMessageContent
        content={'See [note:abc123].'}
        onReferenceClick={onReferenceClick}
      />
    )

    const buttons = await screen.findAllByRole('button')
    expect(buttons.length).toBeGreaterThan(0)
    await userEvent.click(buttons[0])
    expect(onReferenceClick).toHaveBeenCalledWith('note', 'abc123')
  })
})
