import { useEffect, useState } from 'react'

interface DeviceRow {
  id: string; name: string; icon: string
  on: boolean; current_w: number; enabled: boolean
}

interface Props {
  pload: number
}

// Visually distinct palette that reads well on dark navy
const PALETTE = [
  '#f59e0b', // amber
  '#34d399', // emerald
  '#f87171', // rose
  '#60a5fa', // blue
  '#fb923c', // orange
  '#a78bfa', // violet
  '#2dd4bf', // teal
  '#f472b6', // pink
  '#a3e635', // lime
  '#38bdf8', // sky
  '#facc15', // yellow
  '#c084fc', // purple
]

function deviceColor(id: string): string {
  let h = 0
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0
  return PALETTE[h % PALETTE.length]
}

const C_OFF   = '#1e3050'
const C_UNTRK = '#131e2e'
const C_BG    = '#0c1525'
const C_BORD  = '#18283d'

function fmtW(w: number) {
  return w >= 1000 ? `${(w / 1000).toFixed(2)} kW` : `${Math.round(w)} W`
}

export default function DeviceBreakdown({ pload }: Props) {
  const token = localStorage.getItem('gw_token') ?? ''
  const [devices, setDevices] = useState<DeviceRow[]>([])

  useEffect(() => {
    const headers = { Authorization: `Bearer ${token}` }
    function load() {
      fetch('/api/devices', { headers })
        .then(r => r.ok ? r.json() : [])
        .then(setDevices)
        .catch(() => {})
    }
    load()
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [token])

  const enabled    = devices.filter(d => d.enabled)
  if (enabled.length === 0) return null

  const trackedW   = enabled.reduce((s, d) => s + d.current_w, 0)
  const untrackedW = Math.max(0, pload - trackedW)
  const total      = Math.max(pload, trackedW, 1)
  const sorted     = [...enabled].sort((a, b) => b.current_w - a.current_w)

  return (
    <div style={{ background: C_BG, border: `1px solid ${C_BORD}`, borderRadius: 10, padding: '14px 16px' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: '#5a7898' }}>
          Device Breakdown
        </div>
        <div style={{ fontSize: 11, color: '#8ca3c0', fontVariantNumeric: 'tabular-nums' }}>
          <span style={{ fontWeight: 600, color: '#c8ddf0' }}>{fmtW(trackedW)}</span> tracked
          {untrackedW > 20 && (
            <span style={{ color: '#2a3f55' }}> · {fmtW(untrackedW)} other</span>
          )}
        </div>
      </div>

      {/* Stacked bar */}
      <div style={{ display: 'flex', height: 7, borderRadius: 4, overflow: 'hidden', marginBottom: 14, gap: 2 }}>
        {sorted.map(d => {
          const col = d.on ? deviceColor(d.id) : C_OFF
          return (
            <div key={d.id} style={{
              width: `${(d.current_w / total) * 100}%`,
              background: col,
              minWidth: d.current_w > 5 ? 3 : 0,
              transition: 'width 0.4s',
              opacity: d.on ? 1 : 0.4,
            }} />
          )
        })}
        {untrackedW > 20 && (
          <div style={{ flex: 1, background: C_UNTRK, minWidth: 3 }} />
        )}
      </div>

      {/* Rows */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {sorted.map(d => {
          const col  = deviceColor(d.id)
          const pct  = total > 0 ? (d.current_w / total) * 100 : 0
          return (
            <div key={d.id} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {/* Colour swatch */}
              <div style={{
                width: 10, height: 10, borderRadius: 2, flexShrink: 0,
                background: d.on ? col : C_OFF,
                boxShadow: d.on ? `0 0 6px ${col}66` : 'none',
              }} />
              {/* Icon */}
              <span style={{ fontSize: 13, width: 18, textAlign: 'center', flexShrink: 0 }}>{d.icon || '🔌'}</span>
              {/* Name */}
              <span style={{ fontSize: 11, color: d.on ? '#c8ddf0' : '#3a5570', width: 130, flexShrink: 0,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {d.name}
              </span>
              {/* Bar track */}
              <div style={{ flex: 1, height: 5, background: '#111e2e', borderRadius: 2, overflow: 'hidden' }}>
                <div style={{
                  height: '100%',
                  width: `${pct}%`,
                  background: d.on ? col : C_OFF,
                  borderRadius: 2,
                  transition: 'width 0.4s',
                  minWidth: d.current_w > 0 ? 3 : 0,
                  opacity: d.on ? 1 : 0.35,
                }} />
              </div>
              {/* Watts */}
              <span style={{ fontSize: 11, fontWeight: 600,
                color: d.on ? col : '#2a3f55',
                fontVariantNumeric: 'tabular-nums', width: 56, textAlign: 'right', flexShrink: 0 }}>
                {fmtW(d.current_w)}
              </span>
              {/* % */}
              <span style={{ fontSize: 10, color: '#2a3f55', width: 32, textAlign: 'right', flexShrink: 0,
                fontVariantNumeric: 'tabular-nums' }}>
                {pct.toFixed(0)}%
              </span>
            </div>
          )
        })}

        {/* Untracked */}
        {untrackedW > 20 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ width: 10, height: 10, borderRadius: 2, flexShrink: 0, background: C_UNTRK, border: '1px solid #1e3050' }} />
            <span style={{ fontSize: 13, width: 18, textAlign: 'center', flexShrink: 0, color: '#1e3050' }}>?</span>
            <span style={{ fontSize: 11, color: '#2a3f55', width: 130, flexShrink: 0 }}>Untracked</span>
            <div style={{ flex: 1, height: 5, background: '#111e2e', borderRadius: 2, overflow: 'hidden' }}>
              <div style={{
                height: '100%',
                width: `${(untrackedW / total) * 100}%`,
                background: '#1e3050',
                borderRadius: 2,
                transition: 'width 0.4s',
              }} />
            </div>
            <span style={{ fontSize: 11, fontWeight: 600, color: '#2a3f55',
              fontVariantNumeric: 'tabular-nums', width: 56, textAlign: 'right', flexShrink: 0 }}>
              {fmtW(untrackedW)}
            </span>
            <span style={{ fontSize: 10, color: '#1e3050', width: 32, textAlign: 'right', flexShrink: 0,
              fontVariantNumeric: 'tabular-nums' }}>
              {((untrackedW / total) * 100).toFixed(0)}%
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
