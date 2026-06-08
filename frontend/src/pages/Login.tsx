import { useState } from 'react'
import { Sun, Lock } from 'lucide-react'

interface Props { onLogin: (token: string) => void }

export default function Login({ onLogin }: Props) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true); setError('')
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      if (res.ok) {
        const { access_token } = await res.json()
        localStorage.setItem('gw_token', access_token)
        onLogin(access_token)
      } else {
        setError('Invalid password')
      }
    } catch {
      setError('Cannot reach server')
    }
    setLoading(false)
  }

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center mb-8">
          <div className="w-14 h-14 rounded-2xl bg-amber-500 flex items-center justify-center mb-4">
            <Sun size={28} className="text-gray-950" />
          </div>
          <h1 className="text-2xl font-semibold text-white">GoodWe Guru</h1>
          <p className="text-gray-500 text-sm mt-1">Sign in to access your inverter dashboard</p>
        </div>

        <form onSubmit={submit} className="bg-gray-900 border border-gray-800 rounded-2xl p-6 space-y-4">
          <div>
            <label className="block text-sm text-gray-400 mb-1.5">Password</label>
            <div className="relative">
              <Lock size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
              <input
                type="password" value={password} onChange={e => setPassword(e.target.value)}
                placeholder="Enter your password" autoFocus
                className="w-full bg-gray-800 border border-gray-700 rounded-lg pl-9 pr-3 py-2.5 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-amber-500"
              />
            </div>
          </div>
          {error && <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{error}</div>}
          <button type="submit" disabled={loading || !password}
            className="w-full bg-amber-500 hover:bg-amber-400 disabled:opacity-50 text-gray-950 font-semibold py-2.5 rounded-lg transition-colors text-sm">
            {loading ? 'Signing in…' : 'Sign In'}
          </button>
        </form>

        <p className="text-center text-xs text-gray-600 mt-4">
          Password is set during initial setup on the server.
        </p>
      </div>
    </div>
  )
}
