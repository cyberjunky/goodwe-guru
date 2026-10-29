import { Component, StrictMode, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

/** Last line of defence: a render crash on one page (e.g. an API error body
 *  rendered as data) must not blank the whole dashboard. */
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }
  static getDerivedStateFromError(error: Error) { return { error } }
  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="p-6 text-sm text-gray-300 space-y-3">
        <div className="text-red-400 font-semibold">Something went wrong rendering this page.</div>
        <code className="block text-xs text-gray-500">{this.state.error.message}</code>
        <button onClick={() => location.reload()}
          className="bg-amber-500 text-gray-950 font-medium px-3 py-1.5 rounded-lg">Reload</button>
      </div>
    )
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
