import { useState, useEffect } from 'react'
import { BarChart2, RefreshCw } from 'lucide-react'
import { AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend, CartesianGrid } from 'recharts'

type TooltipEntry = { name?: string; value?: number; color?: string }
function EnergyTooltip({ active, payload }: { active?: boolean; payload?: TooltipEntry[] }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-2 text-xs space-y-1">
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color }}>{p.name}: {(p.value ?? 0).toFixed(2)} kWh</div>
      ))}
    </div>
  )
}

type Range = 'today' | '7d' | '30d' | '12m'

interface HistoryEntry {
  ts: string
  e_day: number; e_day_exp: number; e_day_imp: number; e_load_day: number; e_bat_charge_day: number; e_bat_discharge_day: number
  ppv_max: number; pbattery_max: number; pgrid_max: number
}

export default function History() {
  const [range, setRange] = useState<Range>('7d')
  const [data, setData] = useState<HistoryEntry[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => { fetchData() }, [range])

  async function fetchData() {
    setLoading(true)
    try {
      const res = await fetch(`/api/history?range=${range}`)
      const json = await res.json()
      setData(json)
    } catch { setData([]) }
    setLoading(false)
  }

  const totals = data.reduce((acc, d) => ({
    yield: acc.yield + (d.e_day ?? 0),
    export: acc.export + (d.e_day_exp ?? 0),
    import: acc.import + (d.e_day_imp ?? 0),
    load: acc.load + (d.e_load_day ?? 0),
    batCharge: acc.batCharge + (d.e_bat_charge_day ?? 0),
    batDischarge: acc.batDischarge + (d.e_bat_discharge_day ?? 0),
  }), { yield: 0, export: 0, import: 0, load: 0, batCharge: 0, batDischarge: 0 })

  const RANGES: { key: Range; label: string }[] = [
    { key: 'today', label: 'Today' },
    { key: '7d', label: '7 Days' },
    { key: '30d', label: '30 Days' },
    { key: '12m', label: '12 Months' },
  ]

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <BarChart2 className="text-blue-400" size={22} />
          <h1 className="text-xl font-semibold text-white">History</h1>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
            {RANGES.map(r => (
              <button key={r.key} onClick={() => setRange(r.key)}
                className={`px-3 py-1.5 text-sm transition-colors ${range === r.key ? 'bg-amber-500 text-gray-950 font-medium' : 'text-gray-400 hover:text-gray-200'}`}>
                {r.label}
              </button>
            ))}
          </div>
          <button onClick={fetchData} className="p-2 text-gray-500 hover:text-gray-300 transition-colors">
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Totals */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        {[
          { label: 'Solar Yield', value: totals.yield.toFixed(1), color: 'text-amber-400', unit: 'kWh' },
          { label: 'Grid Export', value: totals.export.toFixed(1), color: 'text-emerald-400', unit: 'kWh' },
          { label: 'Grid Import', value: totals.import.toFixed(1), color: 'text-red-400', unit: 'kWh' },
          { label: 'Home Load', value: totals.load.toFixed(1), color: 'text-blue-400', unit: 'kWh' },
          { label: 'Bat Charged', value: totals.batCharge.toFixed(1), color: 'text-emerald-400', unit: 'kWh' },
          { label: 'Bat Discharged', value: totals.batDischarge.toFixed(1), color: 'text-orange-400', unit: 'kWh' },
        ].map(s => (
          <div key={s.label} className="bg-gray-900 border border-gray-800 rounded-xl p-4">
            <div className="text-xs text-gray-500 uppercase tracking-wide mb-1">{s.label}</div>
            <div className={`text-xl font-bold ${s.color}`}>{s.value} <span className="text-sm font-normal text-gray-400">{s.unit}</span></div>
          </div>
        ))}
      </div>

      {/* Energy chart */}
      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6">
        <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-4">Energy Overview (kWh)</h2>
        {data.length > 0 ? (
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis dataKey="ts" tick={{ fontSize: 11, fill: '#6b7280' }} />
              <YAxis tick={{ fontSize: 11, fill: '#6b7280' }} unit=" kWh" width={55} />
              <Tooltip content={<EnergyTooltip />} />
              <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="e_day" name="Solar" fill="#f59e0b" radius={[2, 2, 0, 0]} />
              <Bar dataKey="e_day_exp" name="Export" fill="#22c55e" radius={[2, 2, 0, 0]} />
              <Bar dataKey="e_day_imp" name="Import" fill="#ef4444" radius={[2, 2, 0, 0]} />
              <Bar dataKey="e_load_day" name="Load" fill="#60a5fa" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-[240px] flex items-center justify-center text-gray-600 text-sm">
            {loading ? 'Loading…' : 'No historical data yet. Data is collected while the app is running.'}
          </div>
        )}
      </div>

      {/* Battery chart */}
      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6">
        <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-4">Battery Throughput (kWh)</h2>
        {data.length > 0 ? (
          <ResponsiveContainer width="100%" height={180}>
            <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis dataKey="ts" tick={{ fontSize: 11, fill: '#6b7280' }} />
              <YAxis tick={{ fontSize: 11, fill: '#6b7280' }} unit=" kWh" width={55} />
              <Tooltip content={<EnergyTooltip />} />
              <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
              <Area type="monotone" dataKey="e_bat_charge_day" name="Charged" stroke="#22c55e" fill="#22c55e20" strokeWidth={2} />
              <Area type="monotone" dataKey="e_bat_discharge_day" name="Discharged" stroke="#f97316" fill="#f9731620" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-[180px] flex items-center justify-center text-gray-600 text-sm">No data</div>
        )}
      </div>
    </div>
  )
}
