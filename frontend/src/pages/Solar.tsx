import { useInverter } from '../context/InverterContext'
import StatCard from '../components/StatCard'
import { fmtEnergy } from '../lib/format'
import { Sun, TrendingUp } from 'lucide-react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'

type TooltipEntry = { value?: number }
function PvTooltip({ active, payload }: { active?: boolean; payload?: TooltipEntry[] }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-2 text-xs">
      <div className="text-amber-400">{(payload[0]?.value ?? 0).toFixed(3)} kW</div>
    </div>
  )
}

function StringCard({ n, v, i, p }: { n: number; v: number; i: number; p: number }) {
  const active = p > 5
  return (
    <div className={`bg-gray-900 border rounded-xl p-4 ${active ? 'border-amber-500/30' : 'border-gray-800'}`}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-medium text-gray-300">PV String {n}</span>
        <span className={`text-xs px-2 py-0.5 rounded-full ${active ? 'bg-amber-500/20 text-amber-400' : 'bg-gray-800 text-gray-500'}`}>
          {active ? 'Active' : 'Idle'}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-2 text-center">
        <div>
          <div className="text-xs text-gray-500">Voltage</div>
          <div className="text-lg font-bold text-amber-400">{v.toFixed(1)}</div>
          <div className="text-xs text-gray-500">V</div>
        </div>
        <div>
          <div className="text-xs text-gray-500">Current</div>
          <div className="text-lg font-bold text-amber-300">{i.toFixed(2)}</div>
          <div className="text-xs text-gray-500">A</div>
        </div>
        <div>
          <div className="text-xs text-gray-500">Power</div>
          <div className="text-lg font-bold text-white">{(p / 1000).toFixed(2)}</div>
          <div className="text-xs text-gray-500">kW</div>
        </div>
      </div>
      {active && (
        <div className="mt-3">
          <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
            <div className="h-full bg-amber-400 rounded-full transition-all" style={{ width: `${Math.min(100, (p / 500) * 100)}%` }} />
          </div>
        </div>
      )}
    </div>
  )
}

export default function Solar() {
  const { data } = useInverter()

  if (!data) return <div className="p-6 text-gray-500">No data</div>

  const strings = [
    { n: 1, v: data.vpv1 ?? 0, i: data.ipv1 ?? 0, p: data.ppv1 ?? 0 },
    { n: 2, v: data.vpv2 ?? 0, i: data.ipv2 ?? 0, p: data.ppv2 ?? 0 },
    { n: 3, v: data.vpv3 ?? 0, i: data.ipv3 ?? 0, p: data.ppv3 ?? 0 },
    { n: 4, v: data.vpv4 ?? 0, i: data.ipv4 ?? 0, p: data.ppv4 ?? 0 },
  ].filter(s => s.v > 0 || s.p > 0)

  const pvBars = strings.map(s => ({ name: `PV${s.n}`, kW: +(s.p / 1000).toFixed(3) }))

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center gap-3">
        <Sun className="text-amber-400" size={22} />
        <h1 className="text-xl font-semibold text-white">Solar Production</h1>
      </div>

      {/* Totals */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Total PV Power" value={(data.ppv / 1000).toFixed(2)} unit="kW" color="text-amber-400" icon={<Sun size={14} />} />
        <StatCard label="Today" value={data.e_day?.toFixed(2) ?? '—'} unit="kWh" color="text-amber-300" />
        <StatCard label="This month" value="—" unit="kWh" />
        <StatCard label="All-time" value={data.e_total ? fmtEnergy(data.e_total as number).value : '—'} unit={data.e_total ? fmtEnergy(data.e_total as number).unit : 'kWh'} icon={<TrendingUp size={14} />} />
      </div>

      {/* String cards */}
      <div>
        <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-3">PV Strings</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
          {strings.length > 0
            ? strings.map(s => <StringCard key={s.n} {...s} />)
            : <div className="text-gray-500 text-sm col-span-4">No active PV strings detected</div>
          }
        </div>
      </div>

      {/* Power bar chart */}
      {pvBars.length > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6">
          <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-4">String Comparison (kW)</h2>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={pvBars} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
              <XAxis dataKey="name" tick={{ fontSize: 12, fill: '#9ca3af' }} />
              <YAxis tick={{ fontSize: 11, fill: '#6b7280' }} unit=" kW" />
              <Tooltip content={<PvTooltip />} />
              <Bar dataKey="kW" radius={[4, 4, 0, 0]}>
                {pvBars.map((_, i) => <Cell key={i} fill="#f59e0b" />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Export stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Exported Today" value={data.e_day_exp?.toFixed(2) ?? '—'} unit="kWh" color="text-emerald-400" />
        <StatCard label="Total Exported" value={data.e_total_exp ? (data.e_total_exp / 1000).toFixed(2) : '—'} unit="MWh" color="text-emerald-400" />
        <StatCard label="Bat Charge Today" value={data.e_bat_charge_day?.toFixed(2) ?? '—'} unit="kWh" color="text-blue-400" />
        <StatCard label="Bat Charge Total" value={data.e_bat_charge_total ? (data.e_bat_charge_total / 1000).toFixed(2) : '—'} unit="MWh" color="text-blue-400" />
      </div>
    </div>
  )
}
