import { useState, useEffect } from 'react'
import { Zap, Plus, Trash2, Play, Pencil, CheckCircle2, XCircle, ChevronDown, ChevronUp, Wand2, RefreshCw } from 'lucide-react'

// ─── Types ────────────────────────────────────────────────────────────────────
interface Condition { sensor: string; op: string; value: number; value2: number }
interface Action    { type: string; setting: string; value: unknown; message: string }
interface Automation {
  id: string; name: string; description: string; enabled: boolean
  logic: 'AND' | 'OR'; conditions: Condition[]; actions: Action[]
  cooldown: number; last_triggered: number; trigger_count: number
}
interface Template {
  id: string; name: string; description: string; multi?: boolean
  params?: { key: string; label: string; type: string; min?: number; max?: number; default: number | string }[]
}

// ─── Constants ────────────────────────────────────────────────────────────────
const SENSORS: Record<string, string> = {
  battery_soc: 'Battery SoC (%)', ppv: 'Solar Power (W)',
  pgrid: 'Grid Power (W)', pbattery: 'Battery Power (W)',
  load_ptotal: 'Home Load (W)', temperature: 'Inverter Temp (°C)',
  battery_temperature: 'Battery Temp (°C)',
}
const OPS: Record<string, string> = {
  gt: '>', lt: '<', gte: '≥', lte: '≤', eq: '=', between: 'between',
}
const ACTION_TYPES: Record<string, string> = {
  write_setting: 'Write Setting', set_work_mode: 'Set Work Mode',
  eco_charge: 'Start ECO Charge (all day)', eco_discharge: 'Start ECO Discharge (all day)',
  set_general_mode: 'Switch to General Mode', notify: 'Send Telegram Notification',
}
const WORK_MODE_LABELS: Record<number, string> = {
  0: 'General', 1: 'Off-Grid', 2: 'Backup', 3: 'Eco', 4: 'Peak Shaving',
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function conditionSummary(c: Condition): string {
  const s = SENSORS[c.sensor] ?? c.sensor
  const o = OPS[c.op] ?? c.op
  return c.op === 'between' ? `${s} between ${c.value} – ${c.value2}` : `${s} ${o} ${c.value}`
}
function actionSummary(a: Action): string {
  if (a.type === 'write_setting') return `Set ${a.setting} = ${a.value}`
  if (a.type === 'set_work_mode') return `Work mode → ${WORK_MODE_LABELS[Number(a.value)] ?? a.value}`
  if (a.type === 'notify') return `Notify: "${a.message}"`
  return ACTION_TYPES[a.type] ?? a.type
}
function timeSince(ts: number): string {
  if (!ts) return 'never'
  const d = Math.floor((Date.now() / 1000 - ts) / 60)
  if (d < 2)  return 'just now'
  if (d < 60) return `${d}m ago`
  if (d < 1440) return `${Math.floor(d/60)}h ago`
  return `${Math.floor(d/1440)}d ago`
}

// ─── Sub-components ───────────────────────────────────────────────────────────
function Badge({ on, label, color='text-emerald-400' }: { on: boolean; label: string; color?: string }) {
  return (
    <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${
      on ? `${color} border-current bg-current/10` : 'text-gray-600 border-gray-700'}`}>
      {label}
    </span>
  )
}

function ConditionEditor({ c, onChange, onRemove }:
  { c: Condition; onChange: (c: Condition) => void; onRemove: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2 bg-gray-800/60 border border-gray-700/50 rounded-xl p-3">
      <select value={c.sensor} onChange={e => onChange({ ...c, sensor: e.target.value })}
        className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-white flex-1 min-w-[150px]">
        {Object.entries(SENSORS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select>
      <select value={c.op} onChange={e => onChange({ ...c, op: e.target.value })}
        className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-white w-[90px]">
        {Object.entries(OPS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select>
      <input type="number" value={c.value} onChange={e => onChange({ ...c, value: +e.target.value })}
        className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-white w-[80px]" />
      {c.op === 'between' && <>
        <span className="text-xs text-gray-500">and</span>
        <input type="number" value={c.value2} onChange={e => onChange({ ...c, value2: +e.target.value })}
          className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-white w-[80px]" />
      </>}
      <button onClick={onRemove} className="text-gray-600 hover:text-red-400 transition-colors ml-auto">
        <XCircle size={15} />
      </button>
    </div>
  )
}

function ActionEditor({ a, onChange, onRemove }:
  { a: Action; onChange: (a: Action) => void; onRemove: () => void }) {
  return (
    <div className="flex flex-wrap items-start gap-2 bg-gray-800/60 border border-gray-700/50 rounded-xl p-3">
      <select value={a.type} onChange={e => onChange({ ...a, type: e.target.value })}
        className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-white flex-1 min-w-[200px]">
        {Object.entries(ACTION_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select>
      {a.type === 'write_setting' && <>
        <input placeholder="setting key" value={a.setting} onChange={e => onChange({ ...a, setting: e.target.value })}
          className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-white w-[130px]" />
        <input placeholder="value" value={String(a.value ?? '')} onChange={e => onChange({ ...a, value: isNaN(+e.target.value) ? e.target.value : +e.target.value })}
          className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-white w-[90px]" />
      </>}
      {a.type === 'set_work_mode' && (
        <select value={String(a.value ?? 0)} onChange={e => onChange({ ...a, value: +e.target.value })}
          className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-white w-[150px]">
          {Object.entries(WORK_MODE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      )}
      {a.type === 'notify' && (
        <input placeholder="Telegram message" value={a.message} onChange={e => onChange({ ...a, message: e.target.value })}
          className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-white flex-1 min-w-[200px]" />
      )}
      <button onClick={onRemove} className="text-gray-600 hover:text-red-400 transition-colors">
        <XCircle size={15} />
      </button>
    </div>
  )
}

const BLANK: Omit<Automation, 'id' | 'last_triggered' | 'trigger_count'> = {
  name: '', description: '', enabled: true, logic: 'AND', conditions: [], actions: [], cooldown: 10,
}

function AutomationForm({ initial, onSave, onCancel }:
  { initial?: Partial<Automation>; onSave: (a: typeof BLANK) => void; onCancel: () => void }) {
  const [form, setForm] = useState({ ...BLANK, ...initial })
  const set = (k: string, v: unknown) => setForm(f => ({ ...f, [k]: v }))

  function updateCondition(i: number, c: Condition) {
    const next = [...form.conditions]; next[i] = c; set('conditions', next)
  }
  function removeCondition(i: number) {
    set('conditions', form.conditions.filter((_, j) => j !== i))
  }
  function updateAction(i: number, a: Action) {
    const next = [...form.actions]; next[i] = a; set('actions', next)
  }
  function removeAction(i: number) {
    set('actions', form.actions.filter((_, j) => j !== i))
  }

  const valid = form.name.trim() && form.conditions.length > 0 && form.actions.length > 0

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-2xl p-5 space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label className="block text-xs text-gray-500 mb-1">Name</label>
          <input value={form.name} onChange={e => set('name', e.target.value)} placeholder="Rule name"
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500" />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">Cooldown (min between re-triggers)</label>
          <input type="number" min={1} max={1440} value={form.cooldown}
            onChange={e => set('cooldown', +e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500" />
        </div>
        <div className="md:col-span-2">
          <label className="block text-xs text-gray-500 mb-1">Description (optional)</label>
          <input value={form.description} onChange={e => set('description', e.target.value)}
            placeholder="What does this automation do?"
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500" />
        </div>
      </div>

      {/* Conditions */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
            Conditions
          </span>
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-500">Logic:</span>
            {(['AND','OR'] as const).map(l => (
              <button key={l} onClick={() => set('logic', l)}
                className={`text-xs px-2 py-0.5 rounded border transition-colors ${
                  form.logic === l ? 'bg-amber-500/20 border-amber-500 text-amber-400' : 'border-gray-700 text-gray-500'}`}>
                {l}
              </button>
            ))}
          </div>
        </div>
        <div className="space-y-2">
          {form.conditions.map((c, i) => (
            <ConditionEditor key={i} c={c} onChange={u => updateCondition(i, u)} onRemove={() => removeCondition(i)} />
          ))}
          <button onClick={() => set('conditions', [...form.conditions, { sensor:'battery_soc', op:'gte', value:80, value2:0 }])}
            className="flex items-center gap-1.5 text-xs text-amber-400 hover:text-amber-300 transition-colors">
            <Plus size={13}/> Add condition
          </button>
        </div>
      </div>

      {/* Actions */}
      <div>
        <span className="block text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Actions</span>
        <div className="space-y-2">
          {form.actions.map((a, i) => (
            <ActionEditor key={i} a={a} onChange={u => updateAction(i, u)} onRemove={() => removeAction(i)} />
          ))}
          <button onClick={() => set('actions', [...form.actions, { type:'set_general_mode', setting:'', value:null, message:'' }])}
            className="flex items-center gap-1.5 text-xs text-amber-400 hover:text-amber-300 transition-colors">
            <Plus size={13}/> Add action
          </button>
        </div>
      </div>

      <div className="flex gap-2 pt-2">
        <button onClick={() => onSave(form)} disabled={!valid}
          className="flex items-center gap-2 bg-amber-500 hover:bg-amber-400 disabled:opacity-40 text-gray-950 font-semibold text-sm px-4 py-2 rounded-lg transition-colors">
          <CheckCircle2 size={15}/> Save
        </button>
        <button onClick={onCancel}
          className="text-sm text-gray-400 hover:text-gray-200 px-4 py-2 rounded-lg border border-gray-700 transition-colors">
          Cancel
        </button>
      </div>
    </div>
  )
}

function TemplateCard({ tpl, onInstall }: { tpl: Template; onInstall: (tpl: Template, params: Record<string, number>) => void }) {
  const [open, setOpen]     = useState(false)
  const [params, setParams] = useState<Record<string, number>>(
    Object.fromEntries((tpl.params ?? []).map(p => [p.key.split('.').pop()!, p.default as number]))
  )
  const [busy, setBusy] = useState(false)

  async function install() {
    setBusy(true)
    await onInstall(tpl, params)
    setBusy(false)
    setOpen(false)
  }

  return (
    <div className="bg-gray-900/60 border border-gray-800 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between p-4 cursor-pointer" onClick={() => setOpen(!open)}>
        <div className="flex items-center gap-3">
          <Wand2 size={16} className="text-amber-400 shrink-0" />
          <div>
            <div className="text-sm font-semibold text-gray-200">{tpl.name}</div>
            <div className="text-xs text-gray-500 mt-0.5 line-clamp-1">{tpl.description.split('\n')[0]}</div>
          </div>
        </div>
        {open ? <ChevronUp size={15} className="text-gray-500" /> : <ChevronDown size={15} className="text-gray-500" />}
      </div>

      {open && (
        <div className="px-4 pb-4 space-y-3 border-t border-gray-800/50 pt-3">
          <p className="text-xs text-gray-500 leading-relaxed">{tpl.description}</p>
          {tpl.params && tpl.params.length > 0 && (
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {tpl.params.map(p => (
                <div key={p.key}>
                  <label className="block text-[10px] text-gray-500 mb-0.5">{p.label}</label>
                  <input type={p.type} min={p.min} max={p.max}
                    value={params[p.key.split('.').pop()!] ?? p.default}
                    onChange={e => setParams(prev => ({ ...prev, [p.key.split('.').pop()!]: +e.target.value }))}
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-white focus:outline-none focus:border-amber-500" />
                </div>
              ))}
            </div>
          )}
          <button onClick={install} disabled={busy}
            className="flex items-center gap-2 bg-amber-500/90 hover:bg-amber-500 disabled:opacity-50 text-gray-950 font-semibold text-xs px-4 py-2 rounded-lg transition-colors">
            {busy ? <RefreshCw size={13} className="animate-spin" /> : <Plus size={13} />}
            {tpl.multi ? 'Install automation set' : 'Install automation'}
          </button>
        </div>
      )}
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────
export default function Automations() {
  const [list, setList]         = useState<Automation[]>([])
  const [templates, setTpls]    = useState<Template[]>([])
  const [editing, setEditing]   = useState<Automation | null>(null)
  const [creating, setCreating] = useState(false)
  const [busy, setBusy]         = useState<string | null>(null)
  const [tab, setTab]           = useState<'rules' | 'templates'>('rules')

  const token   = localStorage.getItem('gw_token') ?? ''
  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

  useEffect(() => { loadAll() }, [])

  async function loadAll() {
    const [al, tl] = await Promise.all([
      fetch('/api/automations',           { headers }).then(r => r.json()).catch(() => []),
      fetch('/api/automations/templates', { headers }).then(r => r.json()).catch(() => []),
    ])
    setList(al)
    setTpls(tl)
  }

  async function saveNew(form: typeof BLANK) {
    await fetch('/api/automations', { method: 'POST', headers, body: JSON.stringify(form) })
    setCreating(false)
    loadAll()
  }

  async function saveEdit(form: typeof BLANK) {
    if (!editing) return
    await fetch(`/api/automations/${editing.id}`, { method: 'PUT', headers, body: JSON.stringify(form) })
    setEditing(null)
    loadAll()
  }

  async function toggle(a: Automation) {
    await fetch(`/api/automations/${a.id}`, { method: 'PUT', headers, body: JSON.stringify({ enabled: !a.enabled }) })
    loadAll()
  }

  async function remove(id: string) {
    if (!confirm('Delete this automation?')) return
    await fetch(`/api/automations/${id}`, { method: 'DELETE', headers })
    loadAll()
  }

  async function trigger(id: string) {
    setBusy(id)
    const r = await fetch(`/api/automations/${id}/trigger`, { method: 'POST', headers })
    const j = await r.json()
    setBusy(null)
    alert(`Triggered!\n${j.results?.join('\n') ?? 'done'}`)
  }

  async function installTemplate(tpl: Template, params: Record<string, number>) {
    await fetch('/api/automations/from-template', {
      method: 'POST', headers,
      body: JSON.stringify({ template_id: tpl.id, params }),
    })
    loadAll()
    setTab('rules')
  }

  return (
    <div className="p-4 sm:p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Zap className="text-amber-400 shrink-0" size={22} />
          <div>
            <h1 className="text-xl font-semibold text-white">Automations</h1>
            <p className="text-xs text-gray-500 mt-0.5">
              Rule-based inverter control — emulate Self-Use mode, max/min SoC, peak shaving
            </p>
          </div>
        </div>
        <button onClick={() => { setCreating(true); setTab('rules') }}
          className="flex items-center gap-2 bg-amber-500 hover:bg-amber-400 text-gray-950 font-semibold text-sm px-4 py-2 rounded-lg transition-colors">
          <Plus size={15} /> New rule
        </button>
      </div>

      {/* Info banner */}
      <div className="bg-blue-500/10 border border-blue-500/20 rounded-xl p-4 text-xs text-blue-300 space-y-1">
        <div className="font-semibold mb-1">How automations work</div>
        <div>Rules are evaluated every <strong>30 seconds</strong>. When all conditions match (AND) or any match (OR), actions execute and the rule goes into cooldown.</div>
        <div>Use the <strong>Templates</strong> tab to quickly set up Self-Use emulation, pre-evening charging, or peak shaving with sensible defaults.</div>
      </div>

      {/* Tab bar */}
      <div className="flex bg-gray-900 border border-gray-800 rounded-xl overflow-hidden w-fit">
        {([['rules', `Rules (${list.length})`], ['templates', 'Templates']] as const).map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`px-4 py-2 text-sm transition-colors ${tab === k ? 'bg-amber-500 text-gray-950 font-semibold' : 'text-gray-400 hover:text-gray-200'}`}>
            {l}
          </button>
        ))}
      </div>

      {/* ── Rules tab ────────────────────────────────────── */}
      {tab === 'rules' && (
        <div className="space-y-3">
          {creating && (
            <div className="space-y-2">
              <div className="text-sm font-medium text-gray-400">New automation</div>
              <AutomationForm onSave={saveNew} onCancel={() => setCreating(false)} />
            </div>
          )}

          {list.length === 0 && !creating && (
            <div className="text-center py-16 text-gray-600">
              <Zap size={32} className="mx-auto mb-3 opacity-30" />
              <div className="text-sm mb-1">No automations yet</div>
              <div className="text-xs">Use Templates to get started quickly, or create a custom rule.</div>
            </div>
          )}

          {list.map(a => (
            <div key={a.id} className={`border rounded-2xl overflow-hidden transition-colors ${
              a.enabled ? 'bg-gray-900 border-gray-800' : 'bg-gray-900/40 border-gray-800/50'}`}>

              {editing?.id === a.id ? (
                <div className="p-4">
                  <AutomationForm initial={editing} onSave={saveEdit} onCancel={() => setEditing(null)} />
                </div>
              ) : (
                <div className="p-4">
                  <div className="flex items-start justify-between gap-3 mb-3">
                    <div className="flex items-center gap-2.5 flex-wrap">
                      <span className={`text-sm font-semibold ${a.enabled ? 'text-white' : 'text-gray-500'}`}>{a.name}</span>
                      <Badge on={a.enabled} label={a.enabled ? 'Enabled' : 'Disabled'} />
                      {a.trigger_count > 0 && (
                        <span className="text-[10px] text-gray-500">Triggered {a.trigger_count}× · last {timeSince(a.last_triggered)}</span>
                      )}
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <button onClick={() => trigger(a.id)} disabled={busy === a.id}
                        title="Run now" className="p-1.5 text-gray-500 hover:text-emerald-400 transition-colors">
                        {busy === a.id ? <RefreshCw size={14} className="animate-spin" /> : <Play size={14} />}
                      </button>
                      <button onClick={() => setEditing(a)} title="Edit"
                        className="p-1.5 text-gray-500 hover:text-amber-400 transition-colors">
                        <Pencil size={14} />
                      </button>
                      <button onClick={() => toggle(a)} title={a.enabled ? 'Disable' : 'Enable'}
                        className="p-1.5 text-gray-500 hover:text-blue-400 transition-colors">
                        {a.enabled ? <XCircle size={14} /> : <CheckCircle2 size={14} />}
                      </button>
                      <button onClick={() => remove(a.id)} title="Delete"
                        className="p-1.5 text-gray-500 hover:text-red-400 transition-colors">
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>

                  {a.description && (
                    <p className="text-xs text-gray-500 mb-3 leading-relaxed">{a.description}</p>
                  )}

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div>
                      <div className="text-[10px] font-semibold text-gray-600 uppercase tracking-wider mb-1.5">
                        When ({a.logic})
                      </div>
                      <div className="space-y-1">
                        {a.conditions.map((c, i) => (
                          <div key={i} className="text-xs bg-gray-800/60 border border-gray-700/40 rounded-lg px-2.5 py-1.5 text-gray-300">
                            {conditionSummary(c)}
                          </div>
                        ))}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] font-semibold text-gray-600 uppercase tracking-wider mb-1.5">Then</div>
                      <div className="space-y-1">
                        {a.actions.map((ac, i) => (
                          <div key={i} className="text-xs bg-gray-800/60 border border-gray-700/40 rounded-lg px-2.5 py-1.5 text-gray-300">
                            {actionSummary(ac)}
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-4 mt-3 text-[10px] text-gray-600">
                    <span>Cooldown: {a.cooldown} min</span>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* ── Templates tab ─────────────────────────────────── */}
      {tab === 'templates' && (
        <div className="space-y-3">
          <div className="text-sm text-gray-500">
            Click a template to configure and install it as one or more automation rules.
          </div>
          {templates.map(t => (
            <TemplateCard key={t.id} tpl={t} onInstall={installTemplate} />
          ))}
        </div>
      )}
    </div>
  )
}
