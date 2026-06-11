import { useState, useEffect } from 'react'
import { useInverter } from '../context/InverterContext'
import StatCard from '../components/StatCard'
import { Battery as BatteryIcon, Thermometer, Activity, Cpu, ShieldCheck, BatteryCharging, Sunrise } from 'lucide-react'

/** Depth-of-Discharge readout + Hold/Normal control (uses set_ongrid_battery_dod). */
function DischargeControl() {
  const token = localStorage.getItem('gw_token') ?? ''
  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
  const [dod, setDod] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [sched, setSched] = useState<{ enabled: boolean; threshold_kwh: number; hour_forecast_kwh?: number; producing?: boolean } | null>(null)

  async function refresh() {
    try {
      const r = await fetch('/api/settings', { headers })
      if (r.ok) { const j = await r.json(); if (j.dod !== undefined && j.dod !== null) setDod(Number(j.dod)) }
    } catch { /* ES read can time out; leave as-is */ }
    try {
      const r = await fetch('/api/battery-schedule', { headers })
      if (r.ok) setSched(await r.json())
    } catch { /* ignore */ }
  }
  useEffect(() => { refresh() }, [])

  async function toggleSchedule(enabled: boolean) {
    setSched(s => s ? { ...s, enabled } : s)
    try { await fetch('/api/battery-schedule', { method: 'POST', headers, body: JSON.stringify({ enabled }) }) }
    catch { /* ignore */ }
    refresh()
  }
  async function setThreshold(v: number) {
    if (isNaN(v)) return
    setSched(s => s ? { ...s, threshold_kwh: v } : s)
    try { await fetch('/api/battery-schedule', { method: 'POST', headers, body: JSON.stringify({ threshold_kwh: v }) }) }
    catch { /* ignore */ }
    refresh()
  }

  async function apply(value: number) {
    setBusy(true)
    try {
      const r = await fetch('/api/settings', { method: 'POST', headers, body: JSON.stringify({ key: 'dod', value }) })
      if (r.ok) setDod(value)
    } finally { setBusy(false) }
  }

  const floor = dod === null ? null : 100 - dod
  const holding = dod === 0
  const btn = (active: boolean) =>
    `flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors border ` +
    (active ? 'bg-amber-500 text-gray-950 border-amber-500'
            : 'bg-gray-800 text-gray-300 border-gray-700 hover:border-gray-600')

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6 space-y-3">
      <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide flex items-center gap-2">
        <ShieldCheck size={14} /> Discharge Control
      </h2>
      <div className="text-sm text-gray-300">
        Depth of Discharge: <b>{dod ?? '—'}%</b>
        <span className="text-gray-500"> · won't discharge below <b className="text-gray-300">{floor ?? '—'}%</b> SoC</span>
      </div>
      <div className="flex gap-2">
        <button onClick={() => apply(0)} disabled={busy} className={btn(holding)}>
          <ShieldCheck size={15} /> Hold (no discharge)
        </button>
        <button onClick={() => apply(80)} disabled={busy} className={btn(dod !== null && !holding)}>
          <BatteryCharging size={15} /> Normal (to 20%)
        </button>
      </div>
      <p className="text-[11px] text-gray-500 leading-relaxed">
        <b>Hold</b> sets the floor to 100% — the grid covers the house and the battery is preserved.
        <b> Normal</b> lets the battery power the house down to 20%. (Writes the inverter's on-grid DoD.)
      </p>

      {/* Auto forecast-driven schedule */}
      <div className="border-t border-gray-800 pt-3 mt-1 space-y-2">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-sm text-gray-200 flex items-center gap-2"><Sunrise size={14} /> Auto-hold while solar is forecast</div>
            <div className="text-[11px] text-gray-500 mt-0.5">
              Holds the battery while the hourly solar forecast is above the threshold, discharges below it.
            </div>
          </div>
          <button onClick={() => toggleSchedule(!sched?.enabled)}
            className={`relative inline-flex w-11 h-6 rounded-full transition-colors shrink-0 ${sched?.enabled ? 'bg-amber-500' : 'bg-gray-700'}`}>
            <span className={`inline-block w-4 h-4 bg-white rounded-full shadow transform transition-transform mt-1 ${sched?.enabled ? 'translate-x-6' : 'translate-x-1'}`} />
          </button>
        </div>
        <div className="flex items-center gap-2 text-xs text-gray-400">
          <span>Threshold</span>
          <input type="number" step="0.05" min="0" defaultValue={sched?.threshold_kwh ?? 0.1}
            onBlur={e => setThreshold(parseFloat(e.target.value))}
            className="w-20 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-right text-white" />
          <span>kWh/h</span>
          {sched?.hour_forecast_kwh !== undefined && (
            <span className="ml-auto text-gray-500">
              now: <b className="text-gray-300">{sched.hour_forecast_kwh.toFixed(2)} kWh</b> →{' '}
              <b style={{ color: sched.producing ? '#34d399' : '#fb923c' }}>{sched.producing ? 'Hold' : 'Discharge'}</b>
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

function SocGauge({ soc }: { soc: number }) {
  const color = soc > 60 ? '#22c55e' : soc > 20 ? '#f59e0b' : '#ef4444'
  const segments = 20
  const filled = Math.round((soc / 100) * segments)
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6 flex flex-col items-center gap-4">
      <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide self-start">State of Charge</h2>
      <div className="relative w-40 h-40">
        <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90">
          <circle cx="50" cy="50" r="44" fill="none" stroke="#1f2937" strokeWidth="8" />
          <circle cx="50" cy="50" r="44" fill="none" stroke={color} strokeWidth="8"
            strokeDasharray={`${soc * 2.764} 276.4`} strokeLinecap="round"
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-3xl font-bold text-white">{soc}%</span>
          <span className="text-xs text-gray-500">SoC</span>
        </div>
      </div>
      <div className="flex gap-1">
        {Array.from({ length: segments }).map((_, i) => (
          <div key={i} className="w-3 h-5 rounded-sm" style={{ background: i < filled ? color : '#1f2937' }} />
        ))}
      </div>
    </div>
  )
}

function CellInfo({ label, value, unit, warn }: { label: string; value: number; unit: string; warn?: boolean }) {
  return (
    <div className={`p-3 rounded-lg ${warn ? 'bg-red-500/10 border border-red-500/30' : 'bg-gray-800 border border-gray-700'}`}>
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className={`text-lg font-bold ${warn ? 'text-red-400' : 'text-white'}`}>{value?.toFixed(2)} <span className="text-xs font-normal text-gray-400">{unit}</span></div>
    </div>
  )
}

export default function Battery() {
  const { data } = useInverter()

  if (!data) return <div className="p-6 text-gray-500">No data</div>

  const soc = data.battery_soc ?? 0
  const soh = data.battery_soh ?? 0
  const vbat = data.vbattery1 ?? 0
  const ibat = data.ibattery1 ?? 0
  const pbat = data.pbattery1 ?? 0
  const charging = pbat > 10
  const discharging = pbat < -10

  const maxCellV = data.battery_max_cell_voltage ?? 0
  const minCellV = data.battery_min_cell_voltage ?? 0
  const cellDelta = maxCellV && minCellV ? maxCellV - minCellV : 0

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center gap-3">
        <BatteryIcon className="text-emerald-400" size={22} />
        <h1 className="text-xl font-semibold text-white">Battery</h1>
        <span className={`text-xs px-2 py-0.5 rounded-full ${
          charging ? 'bg-emerald-500/20 text-emerald-400' :
          discharging ? 'bg-orange-500/20 text-orange-400' :
          'bg-gray-800 text-gray-500'
        }`}>
          {data.battery_mode_label ?? 'Unknown'}
        </span>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <SocGauge soc={soc} />

        {/* Core metrics */}
        <div className="xl:col-span-2 grid grid-cols-2 md:grid-cols-3 gap-3">
          <StatCard label="SoH" value={soh} unit="%" color={soh > 80 ? 'text-emerald-400' : soh > 60 ? 'text-amber-400' : 'text-red-400'} />
          <StatCard label="Power" value={(Math.abs(pbat) / 1000).toFixed(2)} unit="kW"
            sub={charging ? '↑ Charging' : discharging ? '↓ Discharging' : 'Idle'}
            color={charging ? 'text-emerald-400' : discharging ? 'text-orange-400' : 'text-gray-400'} />
          <StatCard label="Voltage" value={vbat.toFixed(1)} unit="V" />
          <StatCard label="Current" value={ibat.toFixed(1)} unit="A" color={ibat > 0 ? 'text-emerald-400' : ibat < 0 ? 'text-orange-400' : 'text-gray-400'} />
          <StatCard label="Temperature" value={data.battery_temperature?.toFixed(1) ?? '—'} unit="°C" icon={<Thermometer size={14} />}
            color={(data.battery_temperature ?? 0) > 45 ? 'text-red-400' : 'text-white'} />
          <StatCard label="Charge Limit" value={data.battery_charge_limit ?? '—'} unit="A" />
          <StatCard label="Discharge Limit" value={data.battery_discharge_limit ?? '—'} unit="A" />
          <StatCard label="Discharged Today" value={data.e_bat_discharge_day?.toFixed(2) ?? '—'} unit="kWh" color="text-orange-400" />
          <StatCard label="Charged Today" value={data.e_bat_charge_day?.toFixed(2) ?? '—'} unit="kWh" color="text-emerald-400" />
        </div>
      </div>

      <DischargeControl />

      {/* Cell-level data */}
      {maxCellV > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6">
          <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-4 flex items-center gap-2">
            <Activity size={14} /> Cell Monitoring
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <CellInfo label="Max Cell Voltage" value={maxCellV} unit="V" />
            <CellInfo label="Min Cell Voltage" value={minCellV} unit="V" />
            <CellInfo label="Cell Delta" value={cellDelta} unit="V" warn={cellDelta > 0.1} />
            <CellInfo label="Max Cell Temp" value={data.battery_max_cell_temp ?? 0} unit="°C" warn={(data.battery_max_cell_temp ?? 0) > 45} />
            <CellInfo label="Min Cell Temp" value={data.battery_min_cell_temp ?? 0} unit="°C" />
          </div>
        </div>
      )}

      {/* BMS Data */}
      {data.bms_bat_soc !== undefined && (
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6">
          <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-4 flex items-center gap-2">
            <Cpu size={14} /> BMS Data (Direct)
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard label="BMS SoC" value={data.bms_bat_soc ?? '—'} unit="%" />
            <StatCard label="BMS Voltage" value={(data.bms_bat_voltage ?? 0).toFixed(1)} unit="V" />
            <StatCard label="BMS Current" value={(data.bms_bat_current ?? 0).toFixed(1)} unit="A" />
            <StatCard label="BMS Temp" value={(data.bms_bat_temperature ?? 0).toFixed(1)} unit="°C" />
            <StatCard label="BMS Status" value={data.bms_status ?? '—'} />
          </div>
        </div>
      )}

      {/* External BMS (BeagleBone CAN) */}
      <div className="bg-gray-900 border border-dashed border-gray-700 rounded-2xl p-6">
        <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-2 flex items-center gap-2">
          <Cpu size={14} /> External BMS (CAN Bus via BeagleBone)
        </h2>
        <p className="text-xs text-gray-600">
          Per-cell data from the RS485/CAN interface will appear here once the BeagleBone bridge is connected.
          Data streams via WebSocket to <code className="bg-gray-800 px-1 rounded">/ws/bms</code>.
        </p>
      </div>
    </div>
  )
}
