import { useEffect, useState } from 'react'

interface DeviceRow {
  id: string; name: string; icon: string
  on: boolean; current_w: number; enabled: boolean
}

interface Props {
  pload: number  // total measured load in W — used to show untracked slice
}

const C_ON     = '#a78bfa'  // violet — active device
const C_OFF    = '#2a3f55'  // dim — standby
const C_UNTRK  = '#1e3050'  // very dim — untracked
const C_BG     = '#0c1525'
const C_BORDER = '#18283d'

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

  const enabled = devices.filter(d => d.enabled)
  if (enabled.length === 0) return null

  const trackedW    = enabled.reduce((s, d) => s + d.current_w, 0)
  const untrackedW  = Math.max(0, pload - trackedW)
  const total       = Math.max(pload, trackedW, 1)

  const sorted = [...enabled].sort((a, b) => b.current_w - a.current_w)

  return (
    <div style={{ background: C_BG, border: `1px solid ${C_BORDER}`, borderRadius: 10, padding: '14px 16px' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: '#5a7898' }}>
          Device Breakdown
        </div>
        <div style={{ fontSize: 11, color: '#7c5cbf', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
          {fmtW(trackedW)} tracked
          {untrackedW > 20 && (
            <span style={{ color: '#2a3f55', fontWeight: 400 }}> · {fmtW(untrackedW)} other</span>
          )}
        </div>
      </div>

      {/* Stacked bar */}
      <div style={{ display: 'flex', height: 6, borderRadius: 3, overflow: 'hidden', marginBottom: 14, gap: 1 }}>
        {sorted.map(d => (
          <div key={d.id} style={{
            width: `${(d.current_w / total) * 100}%`,
            background: d.on ? C_ON : C_OFF,
            minWidth: d.current_w > 5 ? 2 : 0,
            transition: 'width 0.4s',
          }} />
        ))}
        {untrackedW > 20 && (
          <div style={{ flex: 1, background: C_UNTRK, minWidth: 2 }} />
        )}
      </div>

      {/* Rows */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        {sorted.map(d => {
          const pct   = total > 0 ? (d.current_w / total) * 100 : 0
          const barW  = Math.max(pct, d.on ? 1 : 0)
          return (
            <div key={d.id} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {/* Icon + name */}
              <span style={{ fontSize: 14, width: 20, textAlign: 'center', flexShrink: 0 }}>{d.icon || '🔌'}</span>
              <span style={{ fontSize: 11, color: d.on ? '#c8ddf0' : '#3a5570', width: 130, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {d.name}
              </span>
              {/* Bar */}
              <div style={{ flex: 1, height: 5, background: '#111e2e', borderRadius: 2, overflow: 'hidden' }}>
                <div style={{
                  height: '100%',
                  width: `${barW}%`,
                  background: d.on ? C_ON : C_OFF,
                  borderRadius: 2,
                  transition: 'width 0.4s',
                  minWidth: d.current_w > 0 ? 3 : 0,
                }} />
              </div>
              {/* Value + pct */}
              <span style={{ fontSize: 11, fontWeight: 600, color: d.on ? C_ON : '#2a3f55',
                fontVariantNumeric: 'tabular-nums', width: 54, textAlign: 'right', flexShrink: 0 }}>
                {fmtW(d.current_w)}
              </span>
              <span style={{ fontSize: 10, color: '#2a3f55', width: 34, textAlign: 'right', flexShrink: 0,
                fontVariantNumeric: 'tabular-nums' }}>
                {pct.toFixed(0)}%
              </span>
            </div>
          )
        })}

        {/* Untracked row */}
        {untrackedW > 20 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 14, width: 20, textAlign: 'center', flexShrink: 0, opacity: 0.4 }}>···</span>
            <span style={{ fontSize: 11, color: '#2a3f55', width: 130, flexShrink: 0 }}>Untracked</span>
            <div style={{ flex: 1, height: 5, background: '#111e2e', borderRadius: 2, overflow: 'hidden' }}>
              <div style={{
                height: '100%',
                width: `${(untrackedW / total) * 100}%`,
                background: C_UNTRK,
                borderRadius: 2,
                transition: 'width 0.4s',
              }} />
            </div>
            <span style={{ fontSize: 11, fontWeight: 600, color: '#2a3f55',
              fontVariantNumeric: 'tabular-nums', width: 54, textAlign: 'right', flexShrink: 0 }}>
              {fmtW(untrackedW)}
            </span>
            <span style={{ fontSize: 10, color: '#1e3050', width: 34, textAlign: 'right', flexShrink: 0,
              fontVariantNumeric: 'tabular-nums' }}>
              {((untrackedW / total) * 100).toFixed(0)}%
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
