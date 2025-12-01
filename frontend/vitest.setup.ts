import React from 'react'
import '@testing-library/jest-dom/vitest'

// Ensure React is globally available for legacy JSX transforms
// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).React = React
