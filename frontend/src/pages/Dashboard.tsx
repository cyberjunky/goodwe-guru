import { useInverter } from '../context/InverterContext'
import EnergyFlow from '../components/EnergyFlow'
import { Sun, Battery, Zap, Home, Activity, Clock, Thermometer, Waves } from 'lucide-react'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'

// ── Colour tokens ─────────────────────────────────────────────────────────────
const T = {
  solar:   '#e8a030',
  batChg:  '#22a875',
  batDis:  '#d07830',
  imp:     '#c85050',
  exp:     '#1a9870',
  load:    '#5070c0',
  neutral: '#8ca3c0',
  bg:      '#09101e',
  card:    '#0c1525',
  cardB:   '#101b2e',
  border:  '#18283d',
  borderHi:'#22334e',
  text:    '#c8ddf0',
  muted:   '#5a7898',
  dim:     '#2e4560',
}

// ── Metric card ───────────────────────────────────────────────────────────────
interface MetricProps {
  label:  string
  value:  string
  unit:   string
  sub?:   string
  color:  string
  icon:   React.ReactNode
  badge?: { text: string; color: string }
}
function MetricCard({ label, value, unit, sub, color, icon, badge }: MetricProps) {
  return (
    <div style={{
      background: T.card,
      border: `1px solid ${T.border}`,
      borderLeft: `3px solid ${color}`,
      borderRadius: 10,
      padding: '14px 16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 4,
      position: 'relative',
    }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
        <span style={{ fontSize:10, fontWeight:700, letterSpacing:'0.12em', textTransform:'uppercase', color: T.muted }}>
          {label}
        </span>
        <span style={{ color: T.dim }}>{icon}</span>
      </div>
      <div style={{ display:'flex', alignItems:'baseline', gap:4 }}>
        <span style={{ fontSize:26, fontWeight:800, lineHeight:1.1, color, fontVariantNumeric:'tabular-nums', letterSpacing:'-0.02em' }}>
          {value}
        </span>
        <span style={{ fontSize:12, color: T.muted, fontWeight:500 }}>{unit}</span>
      </div>
      {sub && <div style={{ fontSize:11, color: T.muted, lineHeight:1.3 }}>{sub}</div>}
      {badge && (
        <div style={{
          position:'absolute', top:12, right:14,
          fontSize:10, fontWeight:700, color: badge.color,
          background: badge.color + '18',
          border: `1px solid ${badge.color}35`,
          borderRadius:5, padding:'2px 7px',
        }}>{badge.text}</div>
      )}
    </div>
  )
}

// ── Info row ──────────────────────────────────────────────────────────────────
function Row({ label, value, unit, color=T.neutral }:
  { label:string; value:string|number; unit?:string; color?:string }) {
  return (
    <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center',
      padding:'6px 0', borderBottom:`1px solid ${T.border}` }}>
      <span style={{ fontSize:11, color: T.muted }}>{label}</span>
      <span style={{ fontSize:12, fontWeight:600, color, fontVariantNumeric:'tabular-nums' }}>
        {value}{unit && <span style={{ fontSize:10, color: T.muted, marginLeft:2 }}>{unit}</span>}
      </span>
    </div>
  )
}

// ── Section header ────────────────────────────────────────────────────────────
function SectionHead({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize:10, fontWeight:700, letterSpacing:'0.12em', textTransform:'uppercase',
      color: T.muted, marginBottom:8, paddingBottom:6, borderBottom:`1px solid ${T.border}` }}>
      {children}
    </div>
  )
}

// ── Tooltip ───────────────────────────────────────────────────────────────────
type TE = { name?:string; value?:number; color?:string }
function Tip({ active, payload }: { active?:boolean; payload?:TE[] }) {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background:'#0c1525', border:`1px solid ${T.border}`, borderRadius:8, padding:'8px 11px', fontSize:11 }}>
      {payload.map((p,i)=>(
        <div key={i} style={{ color:p.color, marginBottom:1 }}>
          {p.name} <strong style={{ color:T.text }}>{p.value?.toFixed(2)} kW</strong>
        </div>
      ))}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
export default function Dashboard() {
  const { data, history, status } = useInverter()

  if (!data) return (
    <div style={{ display:'flex', alignItems:'center', justifyContent:'center',
      height:'100%', flexDirection:'column', gap:8, color:T.muted }}>
      <div style={{ fontSize:15 }}>
        {status==='connecting'?'Connecting to inverter…':'Waiting for data…'}
      </div>
      <div style={{ fontSize:12, color:T.dim }}>
        Ensure the backend is running and the inverter is reachable.
      </div>
    </div>
  )

  const ppv   = (data.ppv       as number) ?? 0
  const pgrid = (data.pgrid     as number) ?? 0
  const pbat  = (data.pbattery1 as number) ?? 0
  const pload = (data.load_ptotal as number) ?? 0
  const soc   = (data.battery_soc as number) ?? 0
  const eDay  = (data.e_day     as number) ?? 0
  const eDayImp = (data.e_day_imp as number) ?? 0
  const eDayExp = (data.e_day_exp as number) ?? 0

  const gImp = pgrid  >  30
  const gExp = pgrid  < -30
  const bChg = pbat   >  30
  const bDis = pbat   < -30

  const chartData = history.slice(-90).map((h, i) => ({
    i, Solar:+(h.ppv/1000).toFixed(3), Battery:+(h.pbattery/1000).toFixed(3),
    Grid:+(h.pgrid/1000).toFixed(3), Load:+(h.load/1000).toFixed(3),
  }))

  return (
    <div style={{ padding:'16px 20px', display:'flex', flexDirection:'column', gap:12, boxSizing:'border-box' }}>

      {/* ── Header ─────────────────────────────────────────────────── */}
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between' }}>
        <div>
          <h1 style={{ margin:0, fontSize:18, fontWeight:700, color:T.text, letterSpacing:'-0.01em' }}>
            Dashboard
          </h1>
          <div style={{ fontSize:11, color:T.muted, marginTop:2 }}>
            {[data.work_mode_label, data.grid_in_out_label].filter(Boolean).join(' · ') || '—'}
          </div>
        </div>
        <div style={{ display:'flex', alignItems:'center', gap:6,
          background:T.card, border:`1px solid ${T.border}`, borderRadius:8,
          padding:'6px 12px', fontSize:11, color:T.muted }}>
          <Clock size={12} color={T.muted}/>
          Today:&nbsp;<strong style={{ color:T.solar }}>{eDay.toFixed(2)} kWh</strong>
        </div>
      </div>

      {/* ── 4 metric cards ─────────────────────────────────────────── */}
      <div style={{ display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:10 }}>
        <MetricCard
          label="Solar" value={(ppv/1000).toFixed(2)} unit="kW"
          sub={`${eDay.toFixed(2)} kWh today`} color={T.solar} icon={<Sun size={15}/>}
          badge={ppv > 50 ? { text:'Active', color:T.solar } : undefined}/>
        <MetricCard
          label="Battery" value={String(soc)} unit="%"
          sub={bChg?`Charging · ${(pbat/1000).toFixed(2)} kW`
              :bDis?`Discharging · ${(Math.abs(pbat)/1000).toFixed(2)} kW`:'Standby'}
          color={bChg?T.batChg:bDis?T.batDis:T.neutral}
          icon={<Battery size={15}/>}/>
        <MetricCard
          label="Grid" value={(Math.abs(pgrid)/1000).toFixed(2)} unit="kW"
          sub={gImp?`Importing · ${eDayImp.toFixed(2)} kWh today`
              :gExp?`Exporting · ${eDayExp.toFixed(2)} kWh today`:'Idle'}
          color={gImp?T.imp:gExp?T.exp:T.neutral}
          icon={<Zap size={15}/>}
          badge={gExp?{text:'↑ Export',color:T.exp}:undefined}/>
        <MetricCard
          label="Home load" value={(pload/1000).toFixed(2)} unit="kW"
          sub={`${(data.e_load_day as number ?? 0).toFixed(2)} kWh today`}
          color={T.load} icon={<Home size={15}/>}/>
      </div>

      {/* ── Main row: flow + panels ─────────────────────────────────── */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12 }}>

        {/* Energy flow */}
        <EnergyFlow ppv={ppv} pbattery={pbat} pgrid={pgrid} pload={pload} soc={soc}/>

        {/* Right panel: inverter stats + totals */}
        <div style={{ display:'flex', flexDirection:'column', gap:10 }}>

          <div style={{ background:T.card, border:`1px solid ${T.border}`, borderRadius:10, padding:'14px 16px', flex:1 }}>
            <SectionHead><Thermometer size={10} style={{display:'inline',marginRight:4}}/>Inverter Status</SectionHead>
            <Row label="Inverter temp"   value={(data.temperature as number??0).toFixed(1)}        unit="°C"
              color={(data.temperature as number??0)>60?T.imp:T.text}/>
            <Row label="Battery temp"    value={(data.battery_temperature as number??0).toFixed(1)} unit="°C"
              color={(data.battery_temperature as number??0)>45?T.imp:T.text}/>
            <Row label="Grid voltage"    value={(data.vgrid as number??0).toFixed(0)}               unit="V"/>
            <Row label="Grid frequency"  value={(data.fgrid as number??0).toFixed(2)}               unit="Hz"/>
            <Row label="Battery voltage" value={(data.vbattery1 as number??0).toFixed(1)}           unit="V"/>
            <Row label="Battery SoH"     value={data.battery_soh as number??'—'}                    unit="%"
              color={(data.battery_soh as number??100)>80?T.batChg:T.batDis}/>
            <Row label="Run hours"       value={(data.h_total as number??0).toLocaleString()}        unit="h"/>
          </div>

          <div style={{ background:T.card, border:`1px solid ${T.border}`, borderRadius:10, padding:'14px 16px' }}>
            <SectionHead><Waves size={10} style={{display:'inline',marginRight:4}}/>All-time Totals</SectionHead>
            <Row label="Total solar yield" value={((data.e_total    as number??0)/1000).toFixed(1)} unit="MWh" color={T.solar}/>
            <Row label="Total exported"    value={((data.e_total_exp as number??0)/1000).toFixed(1)} unit="MWh" color={T.exp}/>
            <Row label="Total imported"    value={((data.e_total_imp as number??0)/1000).toFixed(1)} unit="MWh" color={T.imp}/>
          </div>
        </div>
      </div>

      {/* ── Live chart ─────────────────────────────────────────────── */}
      <div style={{ background:T.card, border:`1px solid ${T.border}`, borderRadius:10, padding:'14px 16px 10px' }}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:10 }}>
          <div style={{ display:'flex', alignItems:'center', gap:5, fontSize:10, fontWeight:700,
            letterSpacing:'0.12em', textTransform:'uppercase', color:T.muted }}>
            <Activity size={11} color={T.muted}/> Live Power
          </div>
          <div style={{ display:'flex', gap:12, fontSize:10 }}>
            {([['Solar',T.solar],['Battery',T.batChg],['Grid',T.imp],['Load',T.load]] as [string,string][])
              .map(([n,c])=> <span key={n} style={{color:c}}>● {n}</span>)}
          </div>
        </div>
        {chartData.length > 3 ? (
          <ResponsiveContainer width="100%" height={150}>
            <AreaChart data={chartData} margin={{top:2,right:0,bottom:0,left:0}}>
              <defs>
                {([['Solar',T.solar],['Battery',T.batChg],['Grid',T.imp],['Load',T.load]] as [string,string][])
                  .map(([n,c])=>(
                  <linearGradient key={n} id={`dg-${n}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor={c} stopOpacity={0.2}/>
                    <stop offset="95%" stopColor={c} stopOpacity={0}/>
                  </linearGradient>
                ))}
              </defs>
              <XAxis dataKey="i" hide/>
              <YAxis tick={{fontSize:10,fill:T.muted}} tickFormatter={v=>`${v}k`} width={32}/>
              <Tooltip content={<Tip/>}/>
              {([['Solar',T.solar],['Battery',T.batChg],['Grid',T.imp],['Load',T.load]] as [string,string][])
                .map(([n,c])=>(
                <Area key={n} type="monotone" dataKey={n} stroke={c} fill={`url(#dg-${n})`}
                  strokeWidth={1.5} dot={false} isAnimationActive={false}/>
              ))}
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div style={{height:150,display:'flex',alignItems:'center',justifyContent:'center',
            fontSize:12,color:T.dim}}>Accumulating live data…</div>
        )}
      </div>

    </div>
  )
}
