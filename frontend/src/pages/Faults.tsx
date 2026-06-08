import { useInverter } from '../context/InverterContext'
import { AlertTriangle, CheckCircle, Info } from 'lucide-react'

const ERROR_BITS: Record<number, string> = {
  0: 'Grid connection fault',
  1: 'AC over-voltage',
  2: 'AC under-voltage',
  3: 'Grid over-frequency',
  4: 'Grid under-frequency',
  5: 'PV over-voltage',
  6: 'PV input over-current',
  7: 'Inverter over-temperature',
  8: 'Over-load fault',
  9: 'PV isolation fault',
  10: 'Residual current fault',
  11: 'DC bus over-voltage',
  12: 'DC bus under-voltage',
  13: 'GFCI fault',
  14: 'HW relay test fault',
  15: 'Battery over-voltage',
  16: 'Battery under-voltage',
  17: 'BMS communication fault',
  18: 'Fan fault',
  19: 'Internal comms fault',
  20: 'Surge protection fault',
  21: 'DC over-current (hardware)',
  22: 'DC input under-voltage',
  23: 'NTC fault',
  24: 'Safety relay fault',
  25: 'Flash fault',
  26: 'Off-grid relay fault',
}

function parseBitmap(codes: string | number | undefined): number[] {
  if (!codes) return []
  const num = typeof codes === 'string' ? parseInt(codes, 16) : codes
  if (isNaN(num) || num === 0) return []
  return Object.keys(ERROR_BITS).map(Number).filter(bit => num & (1 << bit))
}

export default function Faults() {
  const { data } = useInverter()

  const activeFaults = parseBitmap(data?.error_codes as string | number)
  const hasErrors = activeFaults.length > 0

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center gap-3">
        <AlertTriangle className={hasErrors ? 'text-red-400' : 'text-gray-400'} size={22} />
        <h1 className="text-xl font-semibold text-white">Faults & Diagnostics</h1>
      </div>

      {/* Summary badge */}
      <div className={`rounded-xl p-4 border flex items-center gap-3 ${
        hasErrors ? 'bg-red-500/10 border-red-500/30' : 'bg-emerald-500/10 border-emerald-500/30'
      }`}>
        {hasErrors
          ? <AlertTriangle size={20} className="text-red-400 shrink-0" />
          : <CheckCircle size={20} className="text-emerald-400 shrink-0" />
        }
        <div>
          <div className={`font-medium ${hasErrors ? 'text-red-300' : 'text-emerald-300'}`}>
            {hasErrors ? `${activeFaults.length} Active Fault${activeFaults.length > 1 ? 's' : ''}` : 'No Active Faults'}
          </div>
          <div className="text-xs text-gray-500 mt-0.5">
            Work mode: {data?.work_mode_label ?? '—'} · {data?.diagnose_result_label ?? '—'}
          </div>
        </div>
      </div>

      {/* Active faults */}
      {hasErrors && (
        <div className="bg-gray-900 border border-red-500/20 rounded-2xl p-6">
          <h2 className="text-sm font-medium text-red-400 uppercase tracking-wide mb-4">Active Fault Codes</h2>
          <div className="space-y-2">
            {activeFaults.map(bit => (
              <div key={bit} className="flex items-center gap-3 p-3 bg-red-500/10 border border-red-500/20 rounded-lg">
                <AlertTriangle size={16} className="text-red-400 shrink-0" />
                <div>
                  <div className="text-sm font-medium text-red-300">{ERROR_BITS[bit] ?? `Error bit ${bit}`}</div>
                  <div className="text-xs text-gray-500">Error code bit {bit} (0x{(1 << bit).toString(16).toUpperCase()})</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* System status */}
      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6">
        <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-4">System Status</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
          {[
            { label: 'Work Mode', value: data?.work_mode_label },
            { label: 'Grid Status', value: data?.grid_in_out_label },
            { label: 'Battery Mode', value: data?.battery_mode_label },
            { label: 'Diagnostic Result', value: data?.diagnose_result_label },
            { label: 'Safety Standard', value: data?.safety_country_label },
            { label: 'Error Code (raw)', value: data?.error_codes ? `0x${data.error_codes}` : '0x0' },
          ].map(row => (
            <div key={row.label} className="flex justify-between items-center py-2 border-b border-gray-800 last:border-0">
              <span className="text-gray-500">{row.label}</span>
              <span className="text-gray-200 font-medium">{String(row.value ?? '—')}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Temperatures */}
      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6">
        <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-4">Temperature Monitoring</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            { label: 'Inverter', value: data?.temperature },
            { label: 'Air Inlet', value: data?.temperature_air },
            { label: 'Module', value: data?.temperature_module },
            { label: 'Battery', value: data?.battery_temperature },
            { label: 'Max Cell', value: data?.battery_max_cell_temp },
            { label: 'Min Cell', value: data?.battery_min_cell_temp },
          ].map(t => {
            const v = t.value as number
            const warn = v !== undefined && v > 55
            return (
              <div key={t.label} className={`p-3 rounded-xl border ${warn ? 'border-red-500/30 bg-red-500/10' : 'border-gray-700 bg-gray-800'}`}>
                <div className="text-xs text-gray-500 mb-1">{t.label}</div>
                <div className={`text-lg font-bold ${warn ? 'text-red-400' : 'text-white'}`}>
                  {v !== undefined ? `${v.toFixed(1)} °C` : '—'}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-4 flex gap-3 text-xs text-gray-500">
        <Info size={14} className="shrink-0 mt-0.5" />
        Error codes are decoded from the inverter's 32-bit fault register. Raw value: {data?.error_codes ? `0x${data.error_codes}` : '0x00000000'}
      </div>
    </div>
  )
}
