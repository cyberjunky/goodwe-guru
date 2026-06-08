import { useEffect, useState } from 'react'
import { Sankey, Tooltip, ResponsiveContainer, Layer, Rectangle } from 'recharts'
import { Workflow, ChevronLeft, ChevronRight } from 'lucide-react'

interface FlowResp {
  date: string
  links: Record<string, number>
  sources: Record<string, number>
  destinations: Record<string, number>
  samples: number
}

const COLOR: Record<string, string> = {
  Solar: '#f59e0b', Battery: '#34d399', Grid: '#60a5fa', Load: '#fb923c',
}

function todayStr() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
function shiftDate(date: string, days: number) {
  const d = new Date(date + 'T00:00:00')
  d.setDate(d.getDate() + days)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** Coloured Sankey node (label drawn beside it). */
function FlowNode(props: any) {
  const { x, y, width, height, payload, containerWidth } = props
  const c = COLOR[payload.name?.trim()] ?? '#64748b'
  const isLeft = x < (containerWidth ?? 600) / 2
  return (
    <Layer>
      <Rectangle x={x} y={y} width={width} height={height} fill={c} fillOpacity={0.92} />
      {height > 8 && (
        <text x={isLeft ? x + width + 8 : x - 8} y={y + height / 2}
          textAnchor={isLeft ? 'start' : 'end'} dominantBaseline="middle"
          fontSize={11} fontWeight={600} fill="#cbd5e1">
          {payload.name}
        </text>
      )}
    </Layer>
  )
}

export default function Flow() {
  const token = localStorage.getItem('gw_token') ?? ''
  const [date, setDate] = useState(todayStr())
  const [flow, setFlow] = useState<FlowResp | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    fetch(`/api/energy-flow?date=${date}`, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.json()).then(setFlow).catch(() => setFlow(null))
      .finally(() => setLoading(false))
  }, [date, token])

  const L = flow?.links ?? {}
  const rawLinks = [
    { s: 'Solar',    t: 'Load',     v: L.solar_load },
    { s: 'Solar',    t: 'Battery ', v: L.solar_batt },
    { s: 'Solar',    t: 'Grid ',    v: L.solar_grid },
    { s: 'Battery',  t: 'Load',     v: L.batt_load },
    { s: 'Grid',     t: 'Load',     v: L.grid_load },
    { s: 'Grid',     t: 'Battery ', v: L.grid_batt },
  ].filter(l => (l.v ?? 0) > 0.005)

  // Build node list from only the nodes that participate, remap to indices
  const usedKeys = [...new Set(rawLinks.flatMap(l => [l.s, l.t]))]
  const idx = Object.fromEntries(usedKeys.map((k, i) => [k, i]))
  const sankeyData = {
    nodes: usedKeys.map(k => ({ name: k })),
    links: rawLinks.map(l => ({ source: idx[l.s], target: idx[l.t], value: +(l.v as number).toFixed(2) })),
  }

  const srcTotal = Object.values(flow?.sources ?? {}).reduce((a, b) => a + b, 0)
  const dstTotal = Object.values(flow?.destinations ?? {}).reduce((a, b) => a + b, 0)
  const hasData  = rawLinks.length > 0 && srcTotal > 0

  const srcRows = [
    { key: 'solar',   label: 'Solar',   color: COLOR.Solar },
    { key: 'battery', label: 'Battery', color: COLOR.Battery },
    { key: 'grid',    label: 'Grid',    color: COLOR.Grid },
  ]
  const dstRows = [
    { key: 'load',    label: 'Load Usage', color: COLOR.Load },
    { key: 'battery', label: 'Battery',    color: COLOR.Battery },
    { key: 'grid',    label: 'Grid Export',color: COLOR.Grid },
  ]

  const Card = ({ label, color, kwh, total }: { label: string; color: string; kwh: number; total: number }) => (
    <div className="flex items-center justify-between rounded-lg px-3 py-2"
      style={{ background: '#0c1525', border: '1px solid #18283d' }}>
      <div className="flex items-center gap-2">
        <span className="w-2.5 h-2.5 rounded-sm" style={{ background: color }} />
        <span className="text-sm text-gray-300">{label}</span>
      </div>
      <div className="text-right">
        <div className="text-sm font-semibold" style={{ color }}>{kwh.toFixed(2)} <span className="text-[10px] text-gray-500">kWh</span></div>
        <div className="text-[10px] text-gray-500">{total > 0 ? ((kwh / total) * 100).toFixed(1) : '0'}%</div>
      </div>
    </div>
  )

  return (
    <div className="p-4 sm:p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <Workflow className="text-gray-400" size={22} />
          <h1 className="text-xl font-semibold text-white">Energy Flow</h1>
        </div>
        <div className="flex items-center gap-1 rounded-lg" style={{ background: '#0c1525', border: '1px solid #18283d' }}>
          <button onClick={() => setDate(shiftDate(date, -1))} className="p-2 text-gray-400 hover:text-white"><ChevronLeft size={16} /></button>
          <span className="text-sm text-gray-200 px-1 tabular-nums">{date}</span>
          <button onClick={() => setDate(shiftDate(date, 1))} disabled={date >= todayStr()}
            className="p-2 text-gray-400 hover:text-white disabled:opacity-30"><ChevronRight size={16} /></button>
          <button onClick={() => setDate(todayStr())} className="text-xs text-amber-400 px-2 hover:text-amber-300">Today</button>
        </div>
      </div>

      {loading && <div className="text-sm text-gray-500">Loading…</div>}

      {!loading && !hasData && (
        <div className="rounded-xl p-6 text-center text-sm text-gray-500"
          style={{ background: '#0c1525', border: '1px solid #18283d' }}>
          No energy-flow data for {date}.
          {flow && flow.samples === 0 && <div className="mt-1 text-xs">No snapshots were recorded that day (raw data is kept ~7 days).</div>}
        </div>
      )}

      {!loading && hasData && (
        <>
          {/* Totals */}
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-xl p-3 text-center" style={{ background: '#0c1525', border: '1px solid #18283d' }}>
              <div className="text-[11px] text-gray-500 uppercase tracking-wide">Energy in</div>
              <div className="text-2xl font-bold text-white">{srcTotal.toFixed(2)} <span className="text-xs text-gray-500">kWh</span></div>
            </div>
            <div className="rounded-xl p-3 text-center" style={{ background: '#0c1525', border: '1px solid #18283d' }}>
              <div className="text-[11px] text-gray-500 uppercase tracking-wide">Energy out</div>
              <div className="text-2xl font-bold text-white">{dstTotal.toFixed(2)} <span className="text-xs text-gray-500">kWh</span></div>
            </div>
          </div>

          {/* Sankey */}
          <div className="rounded-2xl p-4" style={{ background: '#0c1525', border: '1px solid #18283d' }}>
            <ResponsiveContainer width="100%" height={340}>
              <Sankey
                data={sankeyData}
                nodePadding={28}
                nodeWidth={12}
                margin={{ top: 10, right: 90, bottom: 10, left: 70 }}
                link={{ stroke: '#33415544' } as any}
                node={<FlowNode />}
              >
                <Tooltip
                  contentStyle={{ background: '#0b1220', border: '1px solid #18283d', borderRadius: 8, fontSize: 12 }}
                  formatter={(v: number) => [`${v.toFixed(2)} kWh`, 'Flow']}
                />
              </Sankey>
            </ResponsiveContainer>
          </div>

          {/* Source / destination breakdown */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <div className="text-xs font-medium text-gray-400 uppercase tracking-wide">Energy Source</div>
              {srcRows.map(r => <Card key={r.key} label={r.label} color={r.color} kwh={flow!.sources[r.key] ?? 0} total={srcTotal} />)}
            </div>
            <div className="space-y-2">
              <div className="text-xs font-medium text-gray-400 uppercase tracking-wide">Energy Destination</div>
              {dstRows.map(r => <Card key={r.key} label={r.label} color={r.color} kwh={flow!.destinations[r.key] ?? 0} total={dstTotal} />)}
            </div>
          </div>

          <div className="text-[11px] text-gray-600">
            Derived by integrating live power every {flow ? '~10' : ''}s with a priority model
            (solar → home → battery → grid). Approximate; the inverter doesn't report per-flow energy.
          </div>
        </>
      )}
    </div>
  )
}
