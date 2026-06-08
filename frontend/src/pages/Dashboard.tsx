import { useInverter } from '../context/InverterContext'
import EnergyFlow from '../components/EnergyFlow'
import StatCard from '../components/StatCard'
import { Sun, Zap, Battery, Home, Thermometer, Clock } from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from 'recharts'

const LINES = [
  { key: 'Solar',   color: '#f59e0b' },
  { key: 'Grid',    color: '#ef4444' },
  { key: 'Battery', color: '#22c55e' },
  { key: 'Load',    color: '#60a5fa' },
]

type TooltipEntry = { name?: string; value?: number; color?: string }
function ChartTooltip({ active, payload }: { active?: boolean; payload?: TooltipEntry[] }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-2 text-xs space-y-1">
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color }}>{p.name}: {p.value?.toFixed(2)} kW</div>
      ))}
    </div>
  )
}

export default function Dashboard() {
  const { data, history, status } = useInverter()

  if (!data) return (
    <div className="p-6 flex items-center justify-center h-full">
      <div className="text-center text-gray-500">
        <div className="text-lg mb-2">
          {status === 'connecting' ? 'Connecting to inverter…' : 'No data yet'}
        </div>
        <div className="text-sm">Make sure the backend is running and the inverter IP is configured.</div>
      </div>
    </div>
  )

  const chartData = history.slice(-60).map((h, i) => ({
    t: i,
    Solar:   +(h.ppv / 1000).toFixed(2),
    Grid:    +(h.pgrid / 1000).toFixed(2),
    Battery: +(h.pbattery / 1000).toFixed(2),
    Load:    +(h.load / 1000).toFixed(2),
  }))

  const gridImport = (data.pgrid as number ?? 0) > 0
  const gridExport = (data.pgrid as number ?? 0) < 0
  const batCharging = (data.pbattery1 as number ?? 0) > 0
  const batDischarging = (data.pbattery1 as number ?? 0) < 0

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">Dashboard</h1>
          <p className="text-sm text-gray-500 mt-0.5">{String(data.work_mode_label ?? '—')} · {String(data.safety_country_label ?? '—')}</p>
        </div>
        <div className="flex items-center gap-2 text-xs bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-gray-400">
          <Clock size={13} />
          Today: {(data.e_day as number)?.toFixed(2) ?? '—'} kWh
        </div>
      </div>

      {/* Quick stats row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard
          label="Solar" value={((data.ppv as number) / 1000).toFixed(2)} unit="kW"
          sub={`${(data.e_day as number)?.toFixed(1)} kWh today`}
          color="text-amber-400" icon={<Sun size={16} />}
        />
        <StatCard
          label="Battery" value={data.battery_soc ?? '—'} unit="%"
          sub={batCharging ? `↑ ${((data.pbattery1 as number ?? 0) / 1000).toFixed(2)} kW charging`
            : batDischarging ? `↓ ${(Math.abs(data.pbattery1 as number ?? 0) / 1000).toFixed(2)} kW discharging`
            : 'Idle'}
          color={batCharging ? 'text-emerald-400' : batDischarging ? 'text-orange-400' : 'text-gray-400'}
          icon={<Battery size={16} />}
        />
        <StatCard
          label="Grid" value={(Math.abs(data.pgrid as number ?? 0) / 1000).toFixed(2)} unit="kW"
          sub={gridImport ? `↓ Importing · ${(data.e_day_imp as number)?.toFixed(1)} kWh today`
            : gridExport ? `↑ Exporting · ${(data.e_day_exp as number)?.toFixed(1)} kWh today`
            : 'Idle'}
          color={gridImport ? 'text-red-400' : gridExport ? 'text-emerald-400' : 'text-gray-400'}
          icon={<Zap size={16} />}
        />
        <StatCard
          label="Load" value={((data.load_ptotal as number ?? 0) / 1000).toFixed(2)} unit="kW"
          sub={`${(data.e_load_day as number)?.toFixed(1) ?? '—'} kWh today`}
          color="text-blue-400" icon={<Home size={16} />}
        />
      </div>

      {/* Energy flow + chart */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <EnergyFlow
          ppv={data.ppv as number ?? 0}
          pbattery={data.pbattery1 as number ?? 0}
          pgrid={data.pgrid as number ?? 0}
          pload={data.load_ptotal as number ?? 0}
          soc={data.battery_soc as number ?? 0}
        />

        {/* Live chart */}
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6">
          <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-4">Live Power (kW)</h2>
          {chartData.length > 1 ? (
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={chartData}>
                <XAxis dataKey="t" hide />
                <YAxis tickFormatter={v => `${v}kW`} tick={{ fontSize: 11, fill: '#6b7280' }} width={45} />
                <Tooltip content={<ChartTooltip />} />
                <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
                {LINES.map(({ key, color }) => (
                  <Line key={key} type="monotone" dataKey={key} stroke={color} dot={false} strokeWidth={2} isAnimationActive={false} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-[220px] flex items-center justify-center text-gray-600 text-sm">Accumulating data…</div>
          )}
        </div>
      </div>

      {/* Bottom stats */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <StatCard label="Inverter Temp" value={(data.temperature as number)?.toFixed(1) ?? '—'} unit="°C" icon={<Thermometer size={14} />} />
        <StatCard label="Battery Temp" value={(data.battery_temperature as number)?.toFixed(1) ?? '—'} unit="°C" icon={<Thermometer size={14} />} />
        <StatCard label="Grid Freq" value={(data.fgrid as number)?.toFixed(2) ?? '—'} unit="Hz" />
        <StatCard label="Grid Voltage" value={(data.vgrid as number)?.toFixed(0) ?? '—'} unit="V" />
        <StatCard label="Total Yield" value={(data.e_total as number) ? ((data.e_total as number) / 1000).toFixed(1) : '—'} unit="MWh" />
        <StatCard label="Run Hours" value={(data.h_total as number)?.toLocaleString() ?? '—'} unit="h" />
      </div>
    </div>
  )
}
