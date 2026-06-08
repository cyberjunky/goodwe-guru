import { useState, useEffect } from 'react'
import { Euro, TrendingUp, TrendingDown, Leaf, RefreshCw, BarChart2 } from 'lucide-react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  Legend, LineChart, Line, CartesianGrid, ReferenceLine,
} from 'recharts'

type Range = '7d' | '30d' | '12m'

interface FinRow {
  date: string; ts: string
  e_day: number; e_day_exp: number; e_day_imp: number; e_load_day: number
  import_cost: number; export_revenue: number
  self_consumed_savings: number; bat_savings_value: number
  net_benefit: number; co2_avoided_kg: number
  self_sufficiency_pct: number; self_consumption_pct: number
}

interface Payback {
  system_cost: number; cumulative_savings: number
  pct_recovered: number; remaining: number
}

interface FinData {
  rows: FinRow[]
  totals: Record<string, number>
  payback: Payback | null
  currency: string
}

type TooltipEntry = { name?: string; value?: number; color?: string }
function FinTooltip({ active, payload, label, currency }:
  { active?: boolean; payload?: TooltipEntry[]; label?: string; currency: string }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-3 text-xs space-y-1 min-w-[160px]">
      <div className="text-gray-400 font-medium mb-1">{label}</div>
      {payload.map((p) => (
        <div key={p.name} style={{ color: p.color }} className="flex justify-between gap-4">
          <span>{p.name}</span>
          <span className="font-semibold">{currency}{(p.value ?? 0).toFixed(2)}</span>
        </div>
      ))}
    </div>
  )
}

function MetricCard({ label, value, unit, sub, color, icon }:
  { label: string; value: string; unit?: string; sub?: string; color: string; icon: React.ReactNode }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-gray-500 uppercase tracking-wide">{label}</span>
        <span className={color}>{icon}</span>
      </div>
      <div className={`text-2xl font-bold ${color}`}>
        {value}{unit && <span className="text-sm font-normal text-gray-400 ml-1">{unit}</span>}
      </div>
      {sub && <div className="text-xs text-gray-500 mt-1">{sub}</div>}
    </div>
  )
}

export default function Finance() {
  const [range, setRange]   = useState<Range>('30d')
  const [data, setData]     = useState<FinData | null>(null)
  const [loading, setLoading] = useState(false)
  const token = localStorage.getItem('gw_token') ?? ''

  const headers = { Authorization: `Bearer ${token}` }

  useEffect(() => { load() }, [range])

  async function load() {
    setLoading(true)
    try {
      const res  = await fetch(`/api/financials?range=${range}`, { headers })
      setData(await res.json())
    } catch { /* ignore */ }
    setLoading(false)
  }

  const RANGES: { key: Range; label: string }[] = [
    { key: '7d',  label: '7 days'  },
    { key: '30d', label: '30 days' },
    { key: '12m', label: '12 months' },
  ]

  if (!data) return (
    <div className="p-6 flex items-center justify-center h-64 text-gray-500 text-sm">
      {loading ? 'Loading…' : 'No financial data yet — configure tariffs in Settings first.'}
    </div>
  )

  const { totals: t, currency: cur, payback, rows } = data
  const net = t.net_benefit ?? 0

  const chartData = rows.map(r => ({
    date:     r.ts || r.date,
    'Import cost':     -(r.import_cost ?? 0),
    'Export revenue':   r.export_revenue ?? 0,
    'Solar savings':    r.self_consumed_savings ?? 0,
    'Battery savings':  r.bat_savings_value ?? 0,
    'Net':              r.net_benefit ?? 0,
  }))

  const trendData = rows.map((r) => ({
    date:             r.ts || r.date,
    'Self-sufficiency': r.self_sufficiency_pct ?? 0,
    'Self-consumption': r.self_consumption_pct ?? 0,
  }))

  return (
    <div className="p-4 sm:p-6 space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Euro className="text-emerald-400 shrink-0" size={22} />
          <div>
            <h1 className="text-xl font-semibold text-white">Finance</h1>
            <p className="text-xs text-gray-500">Costs, revenue & savings</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
            {RANGES.map(r => (
              <button key={r.key} onClick={() => setRange(r.key)}
                className={`px-3 py-1.5 text-sm transition-colors ${range === r.key
                  ? 'bg-emerald-500 text-gray-950 font-medium'
                  : 'text-gray-400 hover:text-gray-200'}`}>
                {r.label}
              </button>
            ))}
          </div>
          <button onClick={load} className="p-2 text-gray-500 hover:text-gray-300">
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <MetricCard
          label="Net Benefit"
          value={`${cur}${Math.abs(net).toFixed(2)}`}
          sub={net >= 0 ? 'Earned / saved' : 'Net cost'}
          color={net >= 0 ? 'text-emerald-400' : 'text-red-400'}
          icon={net >= 0 ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
        />
        <MetricCard
          label="Import Cost"
          value={`${cur}${(t.import_cost ?? 0).toFixed(2)}`}
          sub="Paid to grid"
          color="text-red-400"
          icon={<TrendingDown size={16} />}
        />
        <MetricCard
          label="Export Revenue"
          value={`${cur}${(t.export_revenue ?? 0).toFixed(2)}`}
          sub="Sold to grid"
          color="text-blue-400"
          icon={<TrendingUp size={16} />}
        />
        <MetricCard
          label="Solar Savings"
          value={`${cur}${((t.self_consumed_savings ?? 0) + (t.bat_savings_value ?? 0)).toFixed(2)}`}
          sub="Avoided grid import"
          color="text-amber-400"
          icon={<BarChart2 size={16} />}
        />
        <MetricCard
          label="CO₂ Avoided"
          value={(t.co2_avoided_kg ?? 0).toFixed(1)}
          unit="kg"
          sub="vs. grid electricity"
          color="text-green-400"
          icon={<Leaf size={16} />}
        />
        <MetricCard
          label="Self-sufficiency"
          value={`${rows.length ? (rows.reduce((a, r) => a + (r.self_sufficiency_pct ?? 0), 0) / rows.length).toFixed(0) : 0}%`}
          sub="Avg load from own energy"
          color="text-purple-400"
          icon={<TrendingUp size={16} />}
        />
        <MetricCard
          label="Self-consumption"
          value={`${rows.length ? (rows.reduce((a, r) => a + (r.self_consumption_pct ?? 0), 0) / rows.length).toFixed(0) : 0}%`}
          sub="Avg solar consumed locally"
          color="text-amber-400"
          icon={<TrendingUp size={16} />}
        />
      </div>

      {/* Payback tracker */}
      {payback && (
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
          <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-4">
            System Payback Progress
          </h2>
          <div className="flex flex-col sm:flex-row sm:items-center gap-4">
            {/* Progress bar */}
            <div className="flex-1">
              <div className="flex justify-between text-xs text-gray-500 mb-1">
                <span>Recovered: {cur}{payback.cumulative_savings.toFixed(0)}</span>
                <span>System cost: {cur}{payback.system_cost.toFixed(0)}</span>
              </div>
              <div className="h-3 bg-gray-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-emerald-600 to-emerald-400 rounded-full transition-all duration-500"
                  style={{ width: `${payback.pct_recovered}%` }}
                />
              </div>
              <div className="text-xs text-gray-500 mt-1 text-right">
                {cur}{payback.remaining.toFixed(0)} remaining
              </div>
            </div>
            <div className="text-center sm:text-right">
              <div className="text-3xl font-bold text-emerald-400">{payback.pct_recovered}%</div>
              <div className="text-xs text-gray-500">recovered</div>
            </div>
          </div>
        </div>
      )}

      {/* Daily financial breakdown chart */}
      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
        <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-4">
          Daily Cost / Revenue Breakdown ({cur})
        </h2>
        {chartData.length > 0 ? (
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#6b7280' }} />
              <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} tickFormatter={v => `${cur}${v.toFixed(0)}`} width={48} />
              <Tooltip content={<FinTooltip currency={cur} />} />
              <ReferenceLine y={0} stroke="#374151" />
              <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="Import cost"     fill="#ef4444" radius={[2, 2, 0, 0]} stackId="costs" />
              <Bar dataKey="Export revenue"  fill="#60a5fa" radius={[2, 2, 0, 0]} />
              <Bar dataKey="Solar savings"   fill="#f59e0b" radius={[2, 2, 0, 0]} stackId="gains" />
              <Bar dataKey="Battery savings" fill="#34d399" radius={[2, 2, 0, 0]} stackId="gains" />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-[240px] flex items-center justify-center text-gray-600 text-sm">No data</div>
        )}
      </div>

      {/* Net benefit trend */}
      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
        <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-4">
          Net Benefit Trend ({cur}/day)
        </h2>
        {chartData.length > 1 ? (
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#6b7280' }} />
              <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} tickFormatter={v => `${cur}${v.toFixed(2)}`} width={52} />
              <Tooltip content={<FinTooltip currency={cur} />} />
              <ReferenceLine y={0} stroke="#4b5563" strokeDasharray="4 4" />
              <Line type="monotone" dataKey="Net" stroke="#34d399" dot={false} strokeWidth={2} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-[180px] flex items-center justify-center text-gray-600 text-sm">No data</div>
        )}
      </div>

      {/* Self-sufficiency & self-consumption trends */}
      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
        <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-4">
          Efficiency Trends (%)
        </h2>
        {trendData.length > 1 ? (
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={trendData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#6b7280' }} />
              <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: '#6b7280' }} unit="%" width={36} />
              <Tooltip
                contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: 8, fontSize: 11 }}
                formatter={(v: unknown) => [`${(v as number).toFixed(1)}%`]}
              />
              <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 11 }} />
              <Line type="monotone" dataKey="Self-sufficiency" stroke="#a78bfa" dot={false} strokeWidth={2} isAnimationActive={false} />
              <Line type="monotone" dataKey="Self-consumption" stroke="#f59e0b" dot={false} strokeWidth={2} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-[180px] flex items-center justify-center text-gray-600 text-sm">No data</div>
        )}
      </div>
    </div>
  )
}
