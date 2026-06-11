import { useEffect, useState } from 'react'
import { useInverter } from '../context/InverterContext'
import { Settings as SettingsIcon, Save, RefreshCw, AlertCircle, Euro, Bell, Send, CheckCircle2, DownloadCloud, GitCommit, Server, FileText } from 'lucide-react'

const WORK_MODES = [
  { value: 0, label: 'General Mode', desc: 'Standard grid-tied — charges from PV, exports excess' },
  { value: 1, label: 'Off-Grid Mode', desc: 'Island operation — no grid connection' },
  { value: 2, label: 'Backup Mode (EPS)', desc: 'Priority backup power, maintains battery reserve' },
  { value: 3, label: 'Eco Mode', desc: 'Schedule-based charge/discharge windows' },
  { value: 4, label: 'Peak Shaving', desc: 'Limits grid import below configured power threshold' },
  { value: 5, label: 'Self-Use Mode', desc: 'Maximise self-consumption, minimise grid import/export' },
]

const EMS_MODES = [
  { value: 1, label: 'Auto', desc: 'Self-use with meter control' },
  { value: 2, label: 'Charge from PV', desc: 'Charge battery from PV (high) or grid (low)' },
  { value: 3, label: 'Discharge with PV', desc: 'PV + battery both output' },
  { value: 4, label: 'Import from AC', desc: 'Charge from grid (high) or PV (low)' },
  { value: 5, label: 'Export to AC', desc: 'Export — PV preferred, battery backup' },
  { value: 6, label: 'Conserve', desc: 'Off-grid reserve: battery only charges from PV' },
  { value: 7, label: 'Force Off-Grid', desc: 'Forced off-grid operation' },
  { value: 8, label: 'Battery Standby', desc: 'Battery does not charge or discharge' },
]

/** Compact card for use inside a CSS grid — label + description stacked above the control */
function GridSetting({ label, desc, children }: { label: string; desc?: string; children: React.ReactNode }) {
  return (
    <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-3 flex flex-col gap-2">
      <div>
        <div className="text-xs font-medium text-gray-200">{label}</div>
        {desc && <div className="text-[10px] text-gray-500 mt-0.5 leading-tight">{desc}</div>}
      </div>
      <div>{children}</div>
    </div>
  )
}


function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!checked)}
      className={`relative inline-flex w-11 h-6 rounded-full transition-colors ${checked ? 'bg-amber-500' : 'bg-gray-700'}`}
    >
      <span className={`inline-block w-4 h-4 bg-white rounded-full shadow transform transition-transform mt-1 ${checked ? 'translate-x-6' : 'translate-x-1'}`} />
    </button>
  )
}

function NumInput({ value, onChange, min, max, unit }: { value: number; onChange: (v: number) => void; min?: number; max?: number; unit?: string }) {
  return (
    <div className="flex items-center gap-1">
      <input
        type="number" min={min} max={max} value={value}
        onChange={e => onChange(+e.target.value)}
        className="w-24 bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white text-right focus:outline-none focus:border-amber-500"
      />
      {unit && <span className="text-xs text-gray-500">{unit}</span>}
    </div>
  )
}


function EcoSlot({ n, slot, onChange }: { n: number; slot: EcoSlotData; onChange: (s: EcoSlotData) => void }) {
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-medium text-gray-300">Schedule {n}</span>
        <Toggle checked={slot.enabled} onChange={v => onChange({ ...slot, enabled: v })} />
      </div>
      <div className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <div className="text-gray-500 mb-1">Start</div>
          <input type="time" value={slot.start} onChange={e => onChange({ ...slot, start: e.target.value })}
            className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1 text-white text-xs" />
        </div>
        <div>
          <div className="text-gray-500 mb-1">End</div>
          <input type="time" value={slot.end} onChange={e => onChange({ ...slot, end: e.target.value })}
            className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1 text-white text-xs" />
        </div>
        <div className="col-span-2">
          <div className="text-gray-500 mb-1">Power limit (W)</div>
          <input type="number" min={0} max={10000} value={slot.power}
            onChange={e => onChange({ ...slot, power: +e.target.value })}
            className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1 text-white text-xs" />
        </div>
        <div>
          <div className="text-gray-500 mb-1">Min SoC (%)</div>
          <input type="number" min={0} max={100} value={slot.socMin}
            onChange={e => onChange({ ...slot, socMin: +e.target.value })}
            className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1 text-white text-xs" />
        </div>
        <div>
          <div className="text-gray-500 mb-1">Mode</div>
          <select value={slot.charge ? 'charge' : 'discharge'}
            onChange={e => onChange({ ...slot, charge: e.target.value === 'charge' })}
            className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1 text-white text-xs">
            <option value="charge">Charge</option>
            <option value="discharge">Discharge</option>
          </select>
        </div>
      </div>
    </div>
  )
}

interface EcoSlotData { enabled: boolean; start: string; end: string; power: number; socMin: number; charge: boolean }

const DEFAULT_ECO: EcoSlotData = { enabled: false, start: '00:00', end: '06:00', power: 2500, socMin: 10, charge: true }

// ─────────────────────────────────────────────────────────────────────────────
// Tariff settings panel
// ─────────────────────────────────────────────────────────────────────────────
function TariffSettings() {
  const token   = localStorage.getItem('gw_token') ?? ''
  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
  const [cfg, setCfg]   = useState<Record<string, unknown>>({})
  const [saved, setSaved] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    fetch('/api/tariffs', { headers }).then(r => r.json()).then(setCfg).catch(() => {})
  }, [])

  function set(k: string, v: unknown) { setCfg(c => ({ ...c, [k]: v })) }

  async function save() {
    setSaving(true)
    await fetch('/api/tariffs', { method: 'POST', headers, body: JSON.stringify(cfg) })
    setSaving(false); setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  const currency = (cfg.currency as string) ?? '€'

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
          <Euro size={15} /> Electricity Tariffs
        </h2>
        <button onClick={save} disabled={saving}
          className="flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white text-xs px-3 py-1.5 rounded-lg transition-colors">
          {saving ? <RefreshCw size={12} className="animate-spin" /> : saved ? <CheckCircle2 size={12} /> : <Save size={12} />}
          {saved ? 'Saved' : 'Save'}
        </button>
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5 space-y-4">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { key: 'currency',    label: 'Currency symbol', type: 'text',   placeholder: '€' },
            { key: 'import_rate', label: `Import rate (${currency}/kWh)`, type: 'number', step: '0.001' },
            { key: 'export_rate', label: `Export / feed-in (${currency}/kWh)`, type: 'number', step: '0.001' },
            { key: 'vat_pct',    label: 'VAT %', type: 'number', step: '0.1' },
          ].map(f => (
            <div key={f.key}>
              <label className="block text-xs text-gray-500 mb-1">{f.label}</label>
              <input type={f.type} step={(f as {step?: string}).step} placeholder={f.placeholder}
                value={(cfg[f.key] as string | number) ?? ''}
                onChange={e => set(f.key, f.type === 'number' ? +e.target.value : e.target.value)}
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-emerald-500" />
            </div>
          ))}
        </div>

        <div className="flex items-center gap-3">
          <label className="text-sm text-gray-300">Time-of-use pricing</label>
          <button onClick={() => set('tou_enabled', !cfg.tou_enabled)}
            className={`relative inline-flex w-11 h-6 rounded-full transition-colors ${cfg.tou_enabled ? 'bg-emerald-500' : 'bg-gray-700'}`}>
            <span className={`inline-block w-4 h-4 bg-white rounded-full shadow transform transition-transform mt-1 ${cfg.tou_enabled ? 'translate-x-6' : 'translate-x-1'}`} />
          </button>
        </div>

        {!!cfg.tou_enabled && (
          <div className="space-y-2">
            <div className="text-xs text-gray-500">Peak / off-peak periods</div>
            {((cfg.tou_periods as {name: string; start_h: number; start_m: number; end_h: number; end_m: number; rate: number}[]) ?? []).map((p, i) => (
              <div key={i} className="grid grid-cols-2 sm:grid-cols-5 gap-2 bg-gray-800 border border-gray-700 rounded-lg p-3">
                <input value={p.name}
                  onChange={e => {
                    const periods = [...(cfg.tou_periods as typeof p[])]
                    periods[i] = { ...p, name: e.target.value }
                    set('tou_periods', periods)
                  }}
                  className="col-span-2 sm:col-span-1 bg-gray-900 border border-gray-600 rounded px-2 py-1 text-xs text-white" />
                <div className="flex items-center gap-1 text-xs text-gray-400">
                  <span>Start</span>
                  <input type="number" min={0} max={23} value={p.start_h}
                    onChange={e => { const periods = [...(cfg.tou_periods as typeof p[])]; periods[i] = { ...p, start_h: +e.target.value }; set('tou_periods', periods) }}
                    className="w-12 bg-gray-900 border border-gray-600 rounded px-1 py-1 text-xs text-white" />h
                </div>
                <div className="flex items-center gap-1 text-xs text-gray-400">
                  <span>End</span>
                  <input type="number" min={0} max={23} value={p.end_h}
                    onChange={e => { const periods = [...(cfg.tou_periods as typeof p[])]; periods[i] = { ...p, end_h: +e.target.value }; set('tou_periods', periods) }}
                    className="w-12 bg-gray-900 border border-gray-600 rounded px-1 py-1 text-xs text-white" />h
                </div>
                <div className="flex items-center gap-1 text-xs text-gray-400">
                  <span>{currency}</span>
                  <input type="number" step="0.001" value={p.rate}
                    onChange={e => { const periods = [...(cfg.tou_periods as typeof p[])]; periods[i] = { ...p, rate: +e.target.value }; set('tou_periods', periods) }}
                    className="w-16 bg-gray-900 border border-gray-600 rounded px-1 py-1 text-xs text-white" />
                  /kWh
                </div>
              </div>
            ))}
          </div>
        )}

        <div>
          <label className="block text-xs text-gray-500 mb-1">System installation cost ({currency}) — for payback tracking (0 = disabled)</label>
          <input type="number" step="100"
            value={(cfg.system_cost as number) ?? 0}
            onChange={e => set('system_cost', +e.target.value)}
            className="w-40 bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-emerald-500" />
        </div>

        <div>
          <label className="block text-xs text-gray-500 mb-1">Grid CO₂ intensity (kg/kWh) — for carbon tracking</label>
          <input type="number" step="0.001"
            value={(cfg.co2_grid_gkg as number) ?? 0.295}
            onChange={e => set('co2_grid_gkg', +e.target.value)}
            className="w-40 bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-emerald-500" />
        </div>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Telegram notification settings panel
// ─────────────────────────────────────────────────────────────────────────────
function NotificationSettings() {
  const token   = localStorage.getItem('gw_token') ?? ''
  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
  const [cfg, setCfg]       = useState<Record<string, unknown>>({})
  const [saved, setSaved]   = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<'ok' | 'fail' | null>(null)

  useEffect(() => {
    fetch('/api/notifications/config', { headers }).then(r => r.json()).then(setCfg).catch(() => {})
  }, [])

  function set(k: string, v: unknown) { setCfg(c => ({ ...c, [k]: v })) }

  async function save() {
    setSaving(true)
    await fetch('/api/notifications/config', { method: 'POST', headers, body: JSON.stringify(cfg) })
    setSaving(false); setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  async function sendTest() {
    setTesting(true); setTestResult(null)
    try {
      const r = await fetch('/api/notifications/test', { method: 'POST', headers, body: JSON.stringify({ message: '🔔 Test from GoodWe Guru — notifications are working!' }) })
      const j = await r.json()
      setTestResult(j.ok ? 'ok' : 'fail')
    } catch { setTestResult('fail') }
    setTesting(false)
    setTimeout(() => setTestResult(null), 4000)
  }

  const events = [
    { key: 'bat_critical_enabled',      label: 'Battery critical',          desc: `SoC ≤ ${cfg.bat_critical_soc ?? 10}%` },
    { key: 'bat_low_enabled',           label: 'Battery low',               desc: `SoC ≤ ${cfg.bat_low_soc ?? 20}%` },
    { key: 'bat_full_enabled',          label: 'Battery full',              desc: 'SoC reaches 100%' },
    { key: 'fault_enabled',             label: 'Fault code detected',       desc: 'Any inverter fault / fault cleared' },
    { key: 'grid_outage_enabled',       label: 'Grid outage',               desc: 'Switched to backup / grid restored' },
    { key: 'solar_start_stop_enabled',  label: 'Solar start / stop',        desc: 'Production begins & ends each day' },
    { key: 'high_import_enabled',       label: 'High grid import',          desc: `Grid import > ${cfg.high_import_threshold_w ?? 3000} W` },
    { key: 'daily_summary_enabled',     label: 'Daily summary',             desc: `Sent at ${cfg.daily_summary_hour ?? 20}:00` },
  ]

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
          <Bell size={15} /> Telegram Notifications
        </h2>
        <button onClick={save} disabled={saving}
          className="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-xs px-3 py-1.5 rounded-lg transition-colors">
          {saving ? <RefreshCw size={12} className="animate-spin" /> : saved ? <CheckCircle2 size={12} /> : <Save size={12} />}
          {saved ? 'Saved' : 'Save'}
        </button>
      </div>

      <div className="bg-blue-500/10 border border-blue-500/20 rounded-xl p-4 text-xs text-blue-300 space-y-1">
        <div className="font-medium mb-1">Setup instructions:</div>
        <div>1. Message <strong>@BotFather</strong> on Telegram → <code>/newbot</code> → copy the Bot Token</div>
        <div>2. Start a conversation with your bot or add it to a group</div>
        <div>3. Visit <code>https://api.telegram.org/bot&#123;TOKEN&#125;/getUpdates</code> to find your Chat ID</div>
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5 space-y-4">
        <div className="flex items-center gap-3">
          <label className="text-sm text-gray-300">Enable notifications</label>
          <button onClick={() => set('enabled', !cfg.enabled)}
            className={`relative inline-flex w-11 h-6 rounded-full transition-colors ${cfg.enabled ? 'bg-blue-500' : 'bg-gray-700'}`}>
            <span className={`inline-block w-4 h-4 bg-white rounded-full shadow transform transition-transform mt-1 ${cfg.enabled ? 'translate-x-6' : 'translate-x-1'}`} />
          </button>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Bot Token</label>
            <input type="password"
              value={(cfg.bot_token as string) ?? ''}
              placeholder="123456:ABCdef…"
              onChange={e => set('bot_token', e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white font-mono focus:outline-none focus:border-blue-500" />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Chat ID</label>
            <input type="text"
              value={(cfg.chat_id as string) ?? ''}
              placeholder="-1001234567890"
              onChange={e => set('chat_id', e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white font-mono focus:outline-none focus:border-blue-500" />
          </div>
        </div>

        <button onClick={sendTest} disabled={testing || !cfg.bot_token || !cfg.chat_id}
          className="flex items-center gap-2 border border-gray-700 hover:border-blue-500 disabled:opacity-40 text-gray-300 hover:text-blue-400 text-sm px-4 py-2 rounded-lg transition-colors">
          {testing ? <RefreshCw size={14} className="animate-spin" /> : <Send size={14} />}
          Send test message
          {testResult === 'ok'   && <span className="text-emerald-400 text-xs">✓ Delivered!</span>}
          {testResult === 'fail' && <span className="text-red-400 text-xs">✗ Failed — check token/chat ID</span>}
        </button>
      </div>

      {/* Event toggles */}
      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
        <h3 className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-4">Alert Events</h3>
        <div className="space-y-3">
          {events.map(ev => (
            <div key={ev.key} className="flex items-center justify-between py-2 border-b border-gray-800 last:border-0">
              <div>
                <div className="text-sm text-gray-200">{ev.label}</div>
                <div className="text-xs text-gray-500">{ev.desc}</div>
              </div>
              <button onClick={() => set(ev.key, !cfg[ev.key])}
                className={`relative inline-flex w-10 h-5 rounded-full transition-colors shrink-0 ${cfg[ev.key] ? 'bg-blue-500' : 'bg-gray-700'}`}>
                <span className={`inline-block w-3 h-3 bg-white rounded-full shadow transform transition-transform mt-1 ${cfg[ev.key] ? 'translate-x-6' : 'translate-x-1'}`} />
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Threshold settings */}
      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
        <h3 className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-4">Thresholds</h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { key: 'bat_critical_soc',        label: 'Critical SoC',     unit: '%',  min: 0,  max: 30  },
            { key: 'bat_low_soc',             label: 'Low SoC',          unit: '%',  min: 5,  max: 50  },
            { key: 'bat_hysteresis',          label: 'Recovery margin',  unit: '%',  min: 1,  max: 20  },
            { key: 'high_import_threshold_w', label: 'High import',      unit: 'W',  min: 500, max: 15000 },
            { key: 'daily_summary_hour',      label: 'Summary at',       unit: ':00', min: 0,  max: 23  },
          ].map(f => (
            <div key={f.key}>
              <label className="block text-xs text-gray-500 mb-1">{f.label}</label>
              <div className="flex items-center gap-1">
                <input type="number" min={f.min} max={f.max}
                  value={(cfg[f.key] as number) ?? 0}
                  onChange={e => set(f.key, +e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500" />
                <span className="text-xs text-gray-500 shrink-0">{f.unit}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

type SettingsTab = 'inverter' | 'tariffs' | 'notifications' | 'system'

interface VersionInfo { commit?: string; branch?: string; date?: string; subject?: string }
type UpdateState = 'idle' | 'requested' | 'running' | 'ok' | 'failed'

function SystemSettings() {
  const token   = localStorage.getItem('gw_token') ?? ''
  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
  const [ver, setVer]     = useState<VersionInfo>({})
  const [state, setState] = useState<UpdateState>('idle')
  const [message, setMessage] = useState('')
  const [busy, setBusy]   = useState(false)
  const [logText, setLogText] = useState('')
  const [showLog, setShowLog] = useState(false)

  async function loadLog() {
    try {
      const r = await fetch('/api/update/log', { headers })
      if (r.ok) { const j = await r.json(); setLogText(j.log || '(empty)'); setShowLog(true) }
    } catch { /* ignore */ }
  }

  async function refresh() {
    try {
      const r = await fetch('/api/update/status', { headers })
      if (r.ok) {
        const j = await r.json()
        setVer(j.version ?? {})
        if (j.update?.state) setState(j.update.state)
        if (j.update?.message) setMessage(j.update.message)
      }
    } catch {
      // Backend is briefly down while it restarts mid-update — keep showing progress
      setState(s => (s === 'requested' || s === 'running' ? 'running' : s))
    }
  }

  useEffect(() => { refresh() }, [])
  useEffect(() => {
    if (state === 'requested' || state === 'running') {
      const t = setInterval(refresh, 3000)
      return () => clearInterval(t)
    }
  }, [state])

  async function doUpdate() {
    if (!confirm('Update GoodWe Guru to the latest version?\nThe dashboard will rebuild and restart (≈1–2 min).')) return
    setBusy(true)
    try {
      const r = await fetch('/api/update', { method: 'POST', headers })
      if (r.ok) { setState('requested'); setMessage('') }
    } finally { setBusy(false) }
  }

  const busyState = state === 'requested' || state === 'running'

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Server className="text-gray-400" size={22} />
        <h1 className="text-xl font-semibold text-white">System</h1>
      </div>

      {/* Version */}
      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6">
        <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-4">Installed version</h2>
        <div className="flex items-center gap-3 text-sm text-gray-300">
          <GitCommit size={16} className="text-gray-500" />
          {ver.commit
            ? <span className="font-mono">{ver.commit}</span>
            : <span className="text-gray-600">unknown</span>}
          {ver.branch && <span className="text-gray-600">· {ver.branch}</span>}
          {ver.date   && <span className="text-gray-600">· {ver.date}</span>}
        </div>
        {ver.subject && <div className="text-xs text-gray-500 mt-2 leading-snug">{ver.subject}</div>}
      </div>

      {/* Update */}
      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6 space-y-4">
        <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide">Update</h2>
        <p className="text-xs text-gray-500 leading-relaxed">
          Pulls the latest code from GitHub, rebuilds the frontend, and restarts the service.
          The dashboard will be briefly unavailable while it restarts.
        </p>

        <button onClick={doUpdate} disabled={busy || busyState}
          className="flex items-center gap-2 bg-amber-500 hover:bg-amber-400 disabled:opacity-50 text-gray-950 font-medium text-sm px-4 py-2 rounded-lg transition-colors">
          {busyState
            ? <><RefreshCw size={15} className="animate-spin" /> Updating…</>
            : <><DownloadCloud size={15} /> Check for updates &amp; install</>}
        </button>

        {state === 'ok' && (
          <div className="flex items-center gap-2 text-sm text-emerald-400">
            <CheckCircle2 size={15} /> Up to date — running {ver.commit}
          </div>
        )}
        {state === 'failed' && (
          <div className="flex items-center gap-2 text-sm text-red-400">
            <AlertCircle size={15} /> Update failed{message ? ` — ${message}` : ''}.
          </div>
        )}
        {busyState && (
          <div className="text-xs text-gray-500">
            Working… this can take 1–2 minutes. The page may briefly disconnect — it will reconnect automatically.
          </div>
        )}

        <button onClick={() => showLog ? setShowLog(false) : loadLog()}
          className="flex items-center gap-2 text-xs text-gray-400 hover:text-gray-200 transition-colors">
          <FileText size={13} /> {showLog ? 'Hide' : 'View'} update log
        </button>
        {showLog && (
          <pre className="bg-black/40 border border-gray-800 rounded-lg p-3 text-[11px] leading-snug text-gray-300 overflow-auto max-h-80 whitespace-pre-wrap">
            {logText || '(no log yet)'}
          </pre>
        )}
      </div>
    </div>
  )
}

export default function Settings() {
  const [tab, setTab] = useState<SettingsTab>('inverter')
  const { settings, loadSettings, writeSetting } = useInverter()
  const [local, setLocal] = useState<Record<string, unknown>>({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [ecoSlots, setEcoSlots] = useState<EcoSlotData[]>([DEFAULT_ECO, DEFAULT_ECO, DEFAULT_ECO, DEFAULT_ECO])

  useEffect(() => { loadSettings() }, [])
  useEffect(() => { setLocal(settings) }, [settings])

  function set(k: string, v: unknown) { setLocal(prev => ({ ...prev, [k]: v })) }

  async function save() {
    setSaving(true)
    for (const [k, v] of Object.entries(local)) {
      if (v !== settings[k]) await writeSetting(k, v)
    }
    setSaving(false); setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  const wm = (local.work_mode as number) ?? 0
  const emsMode = (local.ems_mode as number) ?? 1
  const isES = (settings.platform as string) === 'ES'

  const TABS: { key: SettingsTab; label: string; icon: React.ReactNode }[] = [
    { key: 'inverter',      label: 'Inverter',      icon: <SettingsIcon size={14} /> },
    { key: 'tariffs',       label: 'Tariffs',       icon: <Euro size={14} /> },
    { key: 'notifications', label: 'Notifications', icon: <Bell size={14} /> },
    { key: 'system',        label: 'System',        icon: <Server size={14} /> },
  ]

  return (
    <div className="p-4 sm:p-6 space-y-6">
      {/* Tab bar */}
      <div className="flex bg-gray-900 border border-gray-800 rounded-xl overflow-hidden w-fit">
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`flex items-center gap-2 px-4 py-2 text-sm transition-colors ${tab === t.key ? 'bg-amber-500 text-gray-950 font-medium' : 'text-gray-400 hover:text-gray-200'}`}>
            {t.icon}{t.label}
          </button>
        ))}
      </div>

      {tab === 'tariffs'       && <TariffSettings />}
      {tab === 'notifications' && <NotificationSettings />}
      {tab === 'system'        && <SystemSettings />}
      {tab === 'inverter'      && <>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <SettingsIcon className="text-gray-400" size={22} />
          <h1 className="text-xl font-semibold text-white">Settings</h1>
        </div>
        <button onClick={save} disabled={saving}
          className="flex items-center gap-2 bg-amber-500 hover:bg-amber-400 disabled:opacity-50 text-gray-950 font-medium text-sm px-4 py-2 rounded-lg transition-colors">
          {saving ? <RefreshCw size={15} className="animate-spin" /> : <Save size={15} />}
          {saved ? 'Saved!' : 'Apply Changes'}
        </button>
      </div>

      <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-4 flex gap-3 text-sm text-amber-300">
        <AlertCircle size={16} className="shrink-0 mt-0.5" />
        Changes are written directly to the inverter via Modbus. Verify settings before applying.
      </div>

      {/* Work Mode */}
      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6">
        <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-4">Operation Mode</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
          {WORK_MODES.map(m => (
            <button key={m.value} onClick={() => set('work_mode', m.value)}
              className={`text-left p-3 rounded-xl border transition-colors ${wm === m.value ? 'border-amber-500 bg-amber-500/10' : 'border-gray-700 bg-gray-800/50 hover:border-gray-600'}`}>
              <div className={`text-sm font-medium ${wm === m.value ? 'text-amber-400' : 'text-gray-300'}`}>{m.label}</div>
              <div className="text-xs text-gray-500 mt-0.5">{m.desc}</div>
            </button>
          ))}
        </div>
      </div>

      {/* EMS Mode — ET/EH/BH/BT platform (745) only — hidden for ES/EM/BP */}
      {(settings.platform as string) !== 'ES' && (
      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6">
        <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-1">EMS Mode</h2>
        <p className="text-xs text-gray-600 mb-4">ET / EH / BH platform only. Controls smart energy management behaviour.</p>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-2">
          {EMS_MODES.map(m => (
            <button key={m.value} onClick={() => set('ems_mode', m.value)}
              className={`text-left p-3 rounded-xl border transition-colors ${emsMode === m.value ? 'border-blue-500 bg-blue-500/10' : 'border-gray-700 bg-gray-800/50 hover:border-gray-600'}`}>
              <div className={`text-sm font-medium ${emsMode === m.value ? 'text-blue-400' : 'text-gray-300'}`}>{m.label}</div>
              <div className="text-xs text-gray-500 mt-0.5">{m.desc}</div>
            </button>
          ))}
        </div>
      </div>
      )}

      {/* Export & Power + Battery — side by side */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">

        {/* Export & Power */}
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
          <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-4">Export & Power Control</h2>
          <div className="grid grid-cols-2 gap-3">
            <GridSetting label="Export Limit" desc="Max export, 0=off">
              <NumInput value={(local.grid_export_limit as number) ?? 0} onChange={v => set('grid_export_limit', v)} min={0} max={15000} unit="W" />
            </GridSetting>
            <GridSetting label="EMS Power Limit" desc="Max EMS charge power">
              <NumInput value={(local.ems_power_limit as number) ?? 0} onChange={v => set('ems_power_limit', v)} min={0} max={15000} unit="W" />
            </GridSetting>
            <GridSetting label="Grid Export" desc="Allow grid export">
              <Toggle checked={!!(local.grid_export)} onChange={v => set('grid_export', v)} />
            </GridSetting>
            <GridSetting label="Shadow Scan" desc="MPPT shade scan">
              <Toggle checked={!!(local.shadow_scan)} onChange={v => set('shadow_scan', v)} />
            </GridSetting>
          </div>
        </div>

        {/* Battery */}
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
          <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-4">Battery Configuration</h2>
          <div className="grid grid-cols-2 gap-3">
            <GridSetting label="Capacity" desc="Usable capacity (Ah)">
              <NumInput value={(local.battery_capacity as number) ?? 100} onChange={v => set('battery_capacity', v)} min={1} max={1000} unit="Ah" />
            </GridSetting>
            <GridSetting label="Max DoD" desc="Min SoC on-grid">
              <NumInput value={(local.battery_discharge_depth as number) ?? 10} onChange={v => set('battery_discharge_depth', v)} min={0} max={95} unit="%" />
            </GridSetting>
            <GridSetting label="Offline DoD" desc="Min SoC during outage">
              <NumInput value={(local.battery_discharge_depth_offline as number) ?? 10} onChange={v => set('battery_discharge_depth_offline', v)} min={0} max={95} unit="%" />
            </GridSetting>
            <GridSetting label="SoC Reserve" desc="Reserve for outages">
              <NumInput value={(local.battery_soc_protection as number) ?? 10} onChange={v => set('battery_soc_protection', v)} min={0} max={50} unit="%" />
            </GridSetting>
            <GridSetting label="Backup / EPS" desc={isES ? 'Read-only on ES — set in SolarGo' : 'Output on grid failure'}>
              {isES
                ? <span className="text-xs text-gray-400">{local.backup_supply ? 'On' : 'Off'} <span className="text-gray-600">(set in SolarGo)</span></span>
                : <Toggle checked={!!(local.backup_supply)} onChange={v => set('backup_supply', v)} />}
            </GridSetting>
            <GridSetting label="Fast Charging" desc="Fast charge (FW19+)">
              <Toggle checked={!!(local.fast_charging)} onChange={v => set('fast_charging', v)} />
            </GridSetting>
            {!!local.fast_charging && <>
              <GridSetting label="Fast Charge SoC" desc="Stop at this SoC">
                <NumInput value={(local.fast_charging_soc as number) ?? 90} onChange={v => set('fast_charging_soc', v)} min={50} max={100} unit="%" />
              </GridSetting>
              <GridSetting label="Fast Charge Power" desc="% of max power">
                <NumInput value={(local.fast_charging_power as number) ?? 100} onChange={v => set('fast_charging_power', v)} min={10} max={100} unit="%" />
              </GridSetting>
            </>}
          </div>
        </div>
      </div>

      {/* Peak shaving + Grid Safety — side by side */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
          <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-4">Peak Shaving</h2>
          <div className="grid grid-cols-2 gap-3">
            <GridSetting label="Power Limit" desc="Max grid import (W)">
              <NumInput value={(local.peak_shaving_power_limit as number) ?? 3000} onChange={v => set('peak_shaving_power_limit', v)} min={0} max={15000} unit="W" />
            </GridSetting>
            <GridSetting label="Min SoC" desc="Only shave above this %">
              <NumInput value={(local.peak_shaving_soc as number) ?? 30} onChange={v => set('peak_shaving_soc', v)} min={0} max={100} unit="%" />
            </GridSetting>
          </div>
        </div>

      {/* Eco schedule */}
      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6">
        <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-4">Eco Schedule (Charge/Discharge Windows)</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {ecoSlots.map((slot, i) => (
            <EcoSlot key={i} n={i + 1} slot={slot} onChange={s => setEcoSlots(prev => prev.map((p, j) => j === i ? s : p))} />
          ))}
        </div>
      </div>

        {/* Grid & Safety — right column of the side-by-side pair */}
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5">
          <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-4">Grid & Safety</h2>
          <div className="grid grid-cols-2 gap-3">
            <GridSetting label="PEN Relay" desc="PE-N relay bonding">
              <Toggle checked={!!(local.pen_relay)} onChange={v => set('pen_relay', v)} />
            </GridSetting>
            <GridSetting label="DRED / Remote Off" desc="Demand response support">
              <Toggle checked={!!(local.dred)} onChange={v => set('dred', v)} />
            </GridSetting>
            <GridSetting label="Unbalanced Output" desc="Allow 3-phase imbalance">
              <Toggle checked={!!(local.unbalanced_output)} onChange={v => set('unbalanced_output', v)} />
            </GridSetting>
          </div>
        </div>
      </div> {/* closes Peak Shaving + Grid Safety grid */}

      </>}
    </div>
  )
}
