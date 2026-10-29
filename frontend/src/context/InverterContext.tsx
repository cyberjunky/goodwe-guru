import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

export interface InverterData {
  // PV
  ppv: number; ppv1: number; ppv2: number; ppv3: number; ppv4: number
  vpv1: number; vpv2: number; vpv3: number; vpv4: number
  ipv1: number; ipv2: number; ipv3: number; ipv4: number
  // Grid
  pgrid: number; vgrid: number; igrid: number; fgrid: number
  pgrid2: number; vgrid2: number; igrid2: number
  pgrid3: number; vgrid3: number; igrid3: number
  grid_in_out_label: string
  // Battery
  pbattery1: number; vbattery1: number; ibattery1: number
  battery_soc: number; battery_soh: number
  battery_mode_label: string; battery_temperature: number
  battery_charge_limit: number; battery_discharge_limit: number
  battery_status: number
  battery_max_cell_voltage: number; battery_min_cell_voltage: number
  battery_max_cell_temp: number; battery_min_cell_temp: number
  // Load/Backup
  load_ptotal: number; backup_ptotal: number
  // Temperatures
  temperature: number; temperature_air: number; temperature_module: number
  // Energy totals
  e_day: number; e_total: number
  e_day_exp: number; e_total_exp: number
  e_day_imp: number; e_total_imp: number
  e_load_day: number; e_load_total: number
  e_bat_charge_day: number; e_bat_charge_total: number
  e_bat_discharge_day: number; e_bat_discharge_total: number
  // Status
  work_mode_label: string; error_codes: string; diagnose_result_label: string
  safety_country_label: string; h_total: number
  // Meter
  meter_active_power1: number; meter_active_power2: number; meter_active_power3: number
  // BMS
  bms_bat_soc: number; bms_bat_voltage: number; bms_bat_current: number
  bms_bat_temperature: number; bms_status: number
  [key: string]: number | string | undefined
}

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'error'

interface InverterContextValue {
  data: InverterData | null
  history: { ts: number; ppv: number; pgrid: number; pbattery: number; load: number }[]
  status: ConnectionStatus
  lastUpdate: Date | null
  settings: Record<string, unknown>
  loadSettings: () => void
  writeSetting: (key: string, value: unknown, reload?: boolean) => Promise<void>
  platform: string   // 'ES' | 'ET' | 'XS' | '' — populated after first connection
}

export const InverterContext = createContext<InverterContextValue | null>(null)

export function InverterProvider({ children, token, onAuthFail }: { children: ReactNode; token: string; onAuthFail: () => void }) {
  const [data, setData] = useState<InverterData | null>(null)
  const [history, setHistory] = useState<InverterContextValue['history']>([])
  const [status, setStatus] = useState<ConnectionStatus>('connecting')
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null)
  const [settings, setSettings] = useState<Record<string, unknown>>({})
  const [platform, setPlatform] = useState<string>('')
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    // `disposed` stops the closed socket's onclose from scheduling a reconnect
    // after cleanup — otherwise an orphan socket keeps reconnecting with the
    // old token (and its 4401 can log out a freshly logged-in user).
    let disposed = false
    connect(() => disposed)
    return () => {
      disposed = true
      wsRef.current?.close()
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
    }
  }, [token])

  function connect(isDisposed: () => boolean) {
    if (isDisposed()) return
    setStatus('connecting')
    const wsUrl = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws/inverter?token=${encodeURIComponent(token)}`
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws



    ws.onopen = async () => {
      setStatus('connected')
      // Fetch platform info once on connect
      try {
        const r = await fetch('/api/status', { headers: { Authorization: `Bearer ${token}` } })
        if (r.ok) { const j = await r.json(); if (j.platform) setPlatform(j.platform) }
      } catch { /* ignore */ }
    }

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data)
      if (msg.type === 'data') {
        setData(msg.payload)
        setLastUpdate(new Date())
        setHistory(prev => {
          const next = [...prev, {
            ts: Date.now(),
            ppv: msg.payload.ppv ?? 0,
            pgrid: msg.payload.pgrid ?? 0,
            pbattery: msg.payload.pbattery1 ?? 0,
            load: msg.payload.load_ptotal ?? 0,
          }]
          return next.slice(-720) // keep last 2h at 10s interval
        })
      }
    }

    ws.onclose = (ev) => {
      if (isDisposed()) return
      if (ev.code === 4401) { onAuthFail(); return }
      setStatus('disconnected')
      reconnectTimer.current = setTimeout(() => connect(isDisposed), 5000)
    }

    ws.onerror = () => setStatus('error')
  }

  const authHeaders = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

  async function loadSettings() {
    try {
      const res = await fetch('/api/settings', { headers: authHeaders })
      if (res.status === 401) { onAuthFail(); return }
      if (res.ok) setSettings(await res.json())
    } catch { /* ignore */ }
  }

  /** Throws on failure — a write the inverter rejected must not look saved.
   *  Pass reload=false when writing several keys, then call loadSettings()
   *  once: each reload is a full AA55 settings read. */
  async function writeSetting(key: string, value: unknown, reload = true) {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: authHeaders,
      body: JSON.stringify({ key, value }),
    })
    if (res.status === 401) onAuthFail()
    if (!res.ok) {
      const detail = await res.json().then(j => j.detail, () => '')
      throw new Error(`${key}: ${detail || res.statusText}`)
    }
    if (reload) await loadSettings()
  }

  // Stable identity: a fresh object every render (i.e. every WebSocket
  // message) re-fired consumers' [settings] effects and wiped unsaved edits.
  const mergedSettings = useMemo(() => ({ ...settings, platform }), [settings, platform])

  return (
    <InverterContext.Provider value={{ data, history, status, lastUpdate, settings: mergedSettings, loadSettings, writeSetting, platform }}>
      {children}
    </InverterContext.Provider>
  )
}

export function useInverter() {
  const ctx = useContext(InverterContext)
  if (!ctx) throw new Error('useInverter must be used within InverterProvider')
  return ctx
}
