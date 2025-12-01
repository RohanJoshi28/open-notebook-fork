import React from 'react'
import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'

import SourceDetailPage from '../page'

const push = vi.fn()

vi.mock('next/navigation', () => {
  return {
    useRouter: () => ({ push }),
    useParams: () => ({ id: encodeURIComponent('source:abc123') }),
  }
})

vi.mock('@/lib/hooks/useSourceChat', () => ({
  useSourceChat: () => ({
    messages: [],
    isStreaming: false,
    contextIndicators: null,
    sendMessage: vi.fn(),
    currentSession: null,
    currentSessionId: null,
    sessions: [],
    createSession: vi.fn(),
    switchSession: vi.fn(),
    updateSession: vi.fn(),
    deleteSession: vi.fn(),
    loadingSessions: false,
  }),
}))

vi.mock('@/components/source/SourceDetailContent', () => ({
  SourceDetailContent: ({ sourceId }: { sourceId: string }) => <div data-testid="source-content">{sourceId}</div>,
}))

vi.mock('@/components/source/ChatPanel', () => ({
  ChatPanel: () => <div data-testid="chat-panel" />, 
}))

vi.mock('@/components/ui/button', () => ({
  Button: ({ children, ...props }: { children: React.ReactNode }) => <button {...props}>{children}</button>,
}))

vi.mock('@/lib/hooks/use-navigation', () => ({
  useNavigation: () => ({
    getReturnPath: () => '/sources',
    getReturnLabel: () => 'Back to Sources',
    clearReturnTo: vi.fn(),
  }),
}))

describe('SourceDetailPage link behavior', () => {
  beforeEach(() => {
    push.mockClear()
  })

  it('renders source content for provided id', () => {
    render(<SourceDetailPage />)
    expect(screen.getByTestId('source-content')).toHaveTextContent('source:abc123')
  })
})
