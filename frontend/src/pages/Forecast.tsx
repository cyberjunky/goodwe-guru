import { useState, useEffect } from 'react'
import { CloudSun, RefreshCw, Info, Settings } from 'lucide-react'
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, ReferenceLine,
} from 'recharts'
import { useInverter } from '../context/InverterContext'

interface HourlyPoint { hour: number; watts: number }
interface DayForecast  { date: string; kwh: number }
interface ForecastResp {
  hourly_today: HourlyPoint[]
  daily:        DayForecast[]
  fetched_at:   number | null
  configured:   boolean
  errors?:      string[]
}
interface ForecastConfig {
  enabled:  boolean
  lat:      number
  lon:      number
  planes:   { label: string; kwp: number; tilt: number; azimuth: number }[]
}

type TooltipEntry = { name?: string; value?: number; color?: string }
function ForecastTooltip({ active, payload, label }:
  { active?: boolean; payload?: TooltipEntry[]; label?: string }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-2 text-xs">
      <div className="text-gray-400 mb-1">{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color }}>
          {p.name}: {p.name?.includes('kWh') || p.name === 'Forecast'
            ? `${(p.value ?? 0).toFixed(2)} kWh`
            : `${((p.value ?? 0) / 1000).toFixed(2)} kW`}
        </div>
      ))}
    </div>
  )
}

function ConfigForm({ config, onSave }: { config: ForecastConfig; onSave: (c: ForecastConfig) => void }) {
  const [local, setLocal] = useState<ForecastConfig>(config)

  function setPlane(i: number, key: string, val: unknown) {
    const planes = local.planes.map((p, j) => j === i ? { ...p, [key]: val } : p)
    setLocal(l => ({ ...l, planes }))
  }

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5 space-y-4">
      <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide flex items-center gap-2">
        <Settings size={14} /> Forecast.Solar Configuration
      </h2>
      <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-3 text-xs text-blue-300 flex gap-2">
        <Info size={13} className="shrink-0 mt-0.5" />
        Uses the free <strong>Forecast.Solar</strong> API — no account needed.
        Results are cached for 30 min.
      </div>
      <div className="flex items-center gap-3">
        <label className="text-sm text-gray-300">Enable forecast</label>
        <button onClick={() => setLocal(l => ({ ...l, enabled: !l.enabled }))}
          className={`relative inline-flex w-11 h-6 rounded-full transition-colors ${local.enabled ? 'bg-amber-500' : 'bg-gray-700'}`}>
          <span className={`inline-block w-4 h-4 bg-white rounded-full shadow transform transition-transform mt-1 ${local.enabled ? 'translate-x-6' : 'translate-x-1'}`} />
        </button>
      </div>
      <div className="grid grid-cols-2 gap-3">
        {[{ label: 'Latitude', key: 'lat' }, { label: 'Longitude', key: 'lon' }].map(f => (
          <div key={f.key}>
            <label className="block text-xs text-gray-500 mb-1">{f.label}</label>
            <input type="number" step="0.001"
              value={(local as unknown as Record<string, number>)[f.key]}
              onChange={e => setLocal(l => ({ ...l, [f.key]: +e.target.value }))}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white" />
          </div>
        ))}
      </div>

      <h3 className="text-xs font-medium text-gray-500 uppercase tracking-wide">Roof Planes / String Orientations</h3>
      {local.planes.map((plane, i) => (
        <div key={i} className="bg-gray-800 border border-gray-700 rounded-xl p-4 space-y-3">
          <div className="flex items-center justify-between">
            <input value={plane.label}
              onChange={e => setPlane(i, 'label', e.target.value)}
              className="bg-transparent text-sm font-medium text-gray-200 border-b border-gray-600 focus:outline-none focus:border-amber-500" />
            {local.planes.length > 1 && (
              <button onClick={() => setLocal(l => ({ ...l, planes: l.planes.filter((_, j) => j !== i) }))}
                className="text-xs text-red-400 hover:text-red-300">Remove</button>
            )}
          </div>
          <div className="grid grid-cols-3 gap-2">
            {[
              { key: 'kwp', label: 'kWp', step: '0.1' },
              { key: 'tilt', label: 'Tilt °', step: '1', min: 0, max: 90 },
              { key: 'azimuth', label: 'Azimuth °', step: '1', min: -180, max: 180, hint: '0=S, -90=E, 90=W' },
            ].map(f => (
              <div key={f.key}>
                <label className="block text-xs text-gray-500 mb-1">{f.label}</label>
                <input type="number" step={f.step}
                  value={(plane as Record<string, unknown>)[f.key] as number}
                  onChange={e => setPlane(i, f.key, +e.target.value)}
                  className="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1 text-xs text-white" />
                {f.hint && <div className="text-[10px] text-gray-600 mt-0.5">{f.hint}</div>}
              </div>
            ))}
          </div>
        </div>
      ))}
      <button
        onClick={() => setLocal(l => ({ ...l, planes: [...l.planes, { label: `Plane ${l.planes.length + 1}`, kwp: 2.0, tilt: 35, azimuth: 0 }] }))}
        className="text-xs text-amber-400 hover:text-amber-300">
        + Add plane
      </button>
      <button onClick={() => onSave(local)}
        className="w-full bg-amber-500 hover:bg-amber-400 text-gray-950 font-semibold py-2 rounded-lg text-sm transition-colors">
        Save & Fetch Forecast
      </button>
    </div>
  )
}

export default function Forecast() {
  const { data: liveData } = useInverter()
  const [forecast, setForecast]   = useState<ForecastResp | null>(null)
  const [config, setConfig]       = useState<ForecastConfig | null>(null)
  const [loading, setLoading]     = useState(false)
  const [showConfig, setShowConfig] = useState(false)
  const token   = localStorage.getItem('gw_token') ?? ''
  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

  useEffect(() => {
    loadConfig()
    loadForecast()
  }, [])

  async function loadConfig() {
    try {
      const res = await fetch('/api/forecast/config', { headers })
      setConfig(await res.json())
    } catch { /* ignore */ }
  }

  async function loadForecast() {
    setLoading(true)
    try {
      const res = await fetch('/api/forecast', { headers })
      const json = await res.json()
      setForecast(json)
      if (!json.configured) setShowConfig(true)
    } catch { /* ignore */ }
    setLoading(false)
  }

  async function saveConfig(cfg: ForecastConfig) {
    await fetch('/api/forecast/config', { method: 'POST', headers, body: JSON.stringify(cfg) })
    setConfig(cfg)
    setShowConfig(false)
    await loadForecast()
  }

  const totalKwp = config?.planes?.reduce((a, p) => a + p.kwp, 0) ?? 0
  const today = forecast?.daily?.[0]
  const tomorrow = forecast?.daily?.[1]

  // Merge hourly forecast with actual production if available
  const livePpv = liveData ? (liveData.ppv as number ?? 0) : 0
  const nowHour = new Date().getHours()
  const hourlyChart = (forecast?.hourly_today ?? []).map(h => ({
    hour:     `${String(h.hour).padStart(2, '0')}:00`,
    Forecast: +(h.watts / 1000).toFixed(3),
    ...(h.hour === nowHour ? { Actual: +(livePpv / 1000).toFixed(3) } : {}),
  }))

  const dailyChart = (forecast?.daily ?? []).map(d => ({
    date:     d.date.slice(5),   // MM-DD
    Forecast: d.kwh,
  }))

  return (
    <div className="p-4 sm:p-6 space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <CloudSun className="text-amber-400 shrink-0" size={22} />
          <div>
            <h1 className="text-xl font-semibold text-white">Solar Forecast</h1>
            <p className="text-xs text-gray-500">
              {totalKwp > 0 ? `${totalKwp.toFixed(1)} kWp installed` : 'Configure your system below'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={loadForecast}
            className="p-2 text-gray-500 hover:text-gray-300 transition-colors">
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
          </button>
          <button onClick={() => setShowConfig(!showConfig)}
            className="flex items-center gap-1.5 text-xs bg-gray-800 border border-gray-700 hover:border-gray-600 px-3 py-1.5 rounded-lg text-gray-400 hover:text-gray-200 transition-colors">
            <Settings size={13} /> Configure
          </button>
        </div>
      </div>

      {/* Config form */}
      {showConfig && config && <ConfigForm config={config} onSave={saveConfig} />}

      {!forecast?.configured && !showConfig && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-4 text-sm text-amber-300 text-center">
          Forecast not configured yet. Click <strong>Configure</strong> to enter your system details.
        </div>
      )}

      {/* Fetch errors from Forecast.Solar (rate limit, bad coords, etc.) */}
      {forecast?.errors && forecast.errors.length > 0 && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 text-sm text-red-300">
          <div className="font-medium mb-1">Forecast.Solar returned an error:</div>
          {forecast.errors.map((e, i) => (
            <div key={i} className="text-xs font-mono text-red-300/90 break-words">{e}</div>
          ))}
          <div className="text-xs text-gray-500 mt-2">
            Common causes: free-tier rate limit (try again in ~15 min), or invalid coordinates.
          </div>
        </div>
      )}

      {/* Today + Tomorrow headline cards */}
      {forecast?.configured && (
        <div className="grid grid-cols-2 gap-3">
          {[
            { label: 'Today', d: today, color: 'text-amber-400', border: 'border-amber-500/30', bg: 'bg-amber-500/5' },
            { label: 'Tomorrow', d: tomorrow, color: 'text-sky-400', border: 'border-sky-500/30', bg: 'bg-sky-500/5' },
          ].map(({ label, d, color, border, bg }) => (
            <div key={label} className={`rounded-xl border ${border} ${bg} p-4 text-center`}>
              <div className="text-xs text-gray-500 mb-1">{label}</div>
              <div className={`text-3xl font-bold ${color}`}>
                {d ? d.kwh.toFixed(1) : '—'}
              </div>
              <div className="text-xs text-gray-400 mt-0.5">kWh expected</div>
              {d && <div className="text-xs text-gray-600 mt-1">{d.date}</div>}
            </div>
          ))}
        </div>
      )}

      {/* Hourly today */}
      {hourlyChart.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
          <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-4">
            Today — Hourly Forecast (kW)
          </h2>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={hourlyChart} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
              <defs>
                <linearGradient id="fc-grad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#f59e0b" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis dataKey="hour" tick={{ fontSize: 10, fill: '#6b7280' }} interval={1} />
              <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} unit=" kW" width={40} />
              <Tooltip content={<ForecastTooltip />} />
              <ReferenceLine x={`${String(nowHour).padStart(2, '0')}:00`}
                stroke="#6b7280" strokeDasharray="4 4" label={{ value: 'Now', fontSize: 10, fill: '#6b7280' }} />
              <Area type="monotone" dataKey="Forecast" stroke="#f59e0b" fill="url(#fc-grad)"
                strokeWidth={2} dot={false} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Multi-day forecast */}
      {dailyChart.length > 1 && (
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
          <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-4">
            Next Days Forecast (kWh)
          </h2>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={dailyChart} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#6b7280' }} />
              <YAxis tick={{ fontSize: 11, fill: '#6b7280' }} unit=" kWh" width={44} />
              <Tooltip content={<ForecastTooltip />} />
              <Bar dataKey="Forecast" fill="#f59e0b" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {forecast?.fetched_at && (
        <p className="text-xs text-gray-600 text-center">
          Forecast data from Forecast.Solar · cached until {new Date((forecast.fetched_at + 1800) * 1000).toLocaleTimeString()}
        </p>
      )}
    </div>
  )
}
