import { useInverter } from '../context/InverterContext'
import StatCard from '../components/StatCard'
import { Zap, TrendingUp, TrendingDown } from 'lucide-react'

function PhaseCard({ phase, v, i, p, f }: { phase: string; v: number; i: number; p: number; f: number }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <div className="text-sm font-semibold text-gray-300 mb-3">Phase {phase}</div>
      <div className="grid grid-cols-2 gap-2">
        <div><div className="text-xs text-gray-500">Voltage</div><div className="font-bold text-white">{v.toFixed(1)} <span className="text-xs text-gray-400">V</span></div></div>
        <div><div className="text-xs text-gray-500">Current</div><div className="font-bold text-white">{i.toFixed(2)} <span className="text-xs text-gray-400">A</span></div></div>
        <div><div className="text-xs text-gray-500">Power</div><div className={`font-bold ${p > 0 ? 'text-red-400' : p < 0 ? 'text-emerald-400' : 'text-gray-400'}`}>{(p / 1000).toFixed(2)} <span className="text-xs text-gray-400">kW</span></div></div>
        <div><div className="text-xs text-gray-500">Freq</div><div className="font-bold text-white">{f.toFixed(2)} <span className="text-xs text-gray-400">Hz</span></div></div>
      </div>
    </div>
  )
}

export default function Grid() {
  const { data } = useInverter()
  if (!data) return <div className="p-6 text-gray-500">No data</div>

  const pgrid = data.pgrid ?? 0
  const importing = pgrid > 0
  const exporting = pgrid < 0

  const phases = [
    { phase: 'L1', v: data.vgrid as number ?? 0, i: data.igrid as number ?? 0, p: data.pgrid as number ?? 0, f: data.fgrid as number ?? 0 },
    { phase: 'L2', v: data.vgrid2 as number ?? 0, i: data.igrid2 as number ?? 0, p: data.pgrid2 as number ?? 0, f: data.fgrid2 as number ?? 0 },
    { phase: 'L3', v: data.vgrid3 as number ?? 0, i: data.igrid3 as number ?? 0, p: data.pgrid3 as number ?? 0, f: data.fgrid3 as number ?? 0 },
  ].filter(ph => ph.v > 0 || ph.p !== 0)

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center gap-3">
        <Zap className="text-yellow-400" size={22} />
        <h1 className="text-xl font-semibold text-white">Grid</h1>
        <span className={`text-xs px-2 py-0.5 rounded-full ${
          importing ? 'bg-red-500/20 text-red-400' : exporting ? 'bg-emerald-500/20 text-emerald-400' : 'bg-gray-800 text-gray-500'
        }`}>
          {data.grid_in_out_label ?? '—'}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard
          label="Grid Power" value={(Math.abs(pgrid) / 1000).toFixed(2)} unit="kW"
          sub={importing ? 'Importing' : exporting ? 'Exporting' : 'Idle'}
          color={importing ? 'text-red-400' : exporting ? 'text-emerald-400' : 'text-gray-400'}
          icon={importing ? <TrendingDown size={14} /> : <TrendingUp size={14} />}
        />
        <StatCard label="Frequency" value={data.fgrid?.toFixed(3) ?? '—'} unit="Hz" />
        <StatCard label="Imported Today" value={data.e_day_imp?.toFixed(2) ?? '—'} unit="kWh" color="text-red-400" />
        <StatCard label="Exported Today" value={data.e_day_exp?.toFixed(2) ?? '—'} unit="kWh" color="text-emerald-400" />
        <StatCard label="Total Imported" value={data.e_total_imp ? (data.e_total_imp / 1000).toFixed(2) : '—'} unit="MWh" color="text-red-400" />
        <StatCard label="Total Exported" value={data.e_total_exp ? (data.e_total_exp / 1000).toFixed(2) : '—'} unit="MWh" color="text-emerald-400" />
        <StatCard label="Safety Standard" value={data.safety_country_label ?? '—'} />
        <StatCard label="Work Mode" value={data.work_mode_label ?? '—'} />
      </div>

      {/* Phases */}
      <div>
        <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-3">Phase Data</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {phases.length > 0
            ? phases.map(ph => <PhaseCard key={ph.phase} {...ph} />)
            : <div className="text-gray-500 text-sm">Single-phase system</div>
          }
        </div>
      </div>

      {/* Meter data */}
      {(data.meter_active_power1 !== undefined) && (
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6">
          <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-4">Smart Meter</h2>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <StatCard label="Meter L1" value={((data.meter_active_power1 ?? 0) / 1000).toFixed(2)} unit="kW" />
            <StatCard label="Meter L2" value={((data.meter_active_power2 ?? 0) / 1000).toFixed(2)} unit="kW" />
            <StatCard label="Meter L3" value={((data.meter_active_power3 ?? 0) / 1000).toFixed(2)} unit="kW" />
          </div>
        </div>
      )}

      {/* Backup/EPS output — ES reports this on the `load` sensors, not pback_up */}
      {(() => {
        const vl = Number(data.vload ?? 0)
        const il = Number(data.iload ?? 0)
        const pw = vl * il                       // backup terminal power (V×A) — pload is whole-house
        const live = vl > 50
        return (
          <div className="bg-gray-900 border border-amber-500/20 rounded-2xl p-6">
            <h2 className="text-sm font-medium text-amber-400 uppercase tracking-wide mb-4">Backup / EPS Output</h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatCard label="Output Power"
                value={pw >= 1000 ? (pw / 1000).toFixed(2) : pw.toFixed(0)}
                unit={pw >= 1000 ? 'kW' : 'W'} color="text-amber-400" />
              <StatCard label="Voltage" value={vl.toFixed(0)} unit="V" color={live ? 'text-amber-400' : 'text-gray-500'} />
              <StatCard label="Current" value={il.toFixed(1)} unit="A" />
              <StatCard label="Frequency" value={Number(data.fload ?? 0).toFixed(2)} unit="Hz" />
            </div>
            <p className="text-[11px] text-gray-500 mt-2">
              {String(data.load_mode_label ?? '')}{live ? ' · energised' : ' · not energised'} · power = V×A (approx; pload tracks the whole house)
            </p>
          </div>
        )
      })()}
    </div>
  )
}
