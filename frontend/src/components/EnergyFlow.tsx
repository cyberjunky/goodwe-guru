/**
 * EnergyFlow — animated energy diagram.
 *
 * Direction is shown via "marching dash" stroke animation on each arm.
 * Dashes travel from source to destination — unambiguous at any power level.
 *
 * Directions (path defined from satellite node TO hub):
 *   Solar   : always node→hub
 *   Grid    : node→hub = import (pgrid>0), hub→node = export (pgrid<0)
 *   Battery : hub→node = charging (pbattery>0), node→hub = discharging (<0)
 *   Home    : always hub→node
 */
import { useEffect, useRef } from 'react'

interface Props {
  ppv: number; pbattery: number; pgrid: number; pload: number; soc: number
}

// ── Layout ────────────────────────────────────────────────────────────────────
const W = 480, H = 380, CX = W / 2, CY = H / 2
const NR = 54  // node radius
const NODE = {
  solar: { x: CX,      y: 52  },
  grid:  { x: 70,      y: CY  },
  home:  { x: W - 70,  y: CY  },
  bat:   { x: CX,      y: H - 52 },
}
// Paths: each goes FROM satellite node TO hub centre
const G = NR + 8, HG = 8
const PATHS = {
  solar: `M ${CX},${NODE.solar.y + G} Q ${CX - 22},${CY - 55} ${CX},${CY - HG}`,
  grid:  `M ${NODE.grid.x + G},${CY} Q ${CX - 55},${CY + 22} ${CX - HG},${CY}`,
  home:  `M ${NODE.home.x - G},${CY} Q ${CX + 55},${CY - 22} ${CX + HG},${CY}`,
  bat:   `M ${CX},${NODE.bat.y - G} Q ${CX + 22},${CY + 55} ${CX},${CY + HG}`,
} as const
type ArmKey = keyof typeof PATHS

// ── Colour palette ────────────────────────────────────────────────────────────
const COL = { solar:'#f0a820', grid_imp:'#e06058', grid_exp:'#00c49a',
               home:'#7b6ef0', bat_chg:'#00c49a', bat_dis:'#e0881c' }

// ── Helpers ───────────────────────────────────────────────────────────────────
const fmt = (w:number) => { const a=Math.abs(w); return a>=1000?`${(a/1000).toFixed(2)} kW`:`${Math.round(a)} W` }

// Dash animation period: faster = more power
function period(watts: number) {
  const a = Math.abs(watts)
  if (a < 30)   return 0
  if (a < 300)  return 2.6
  if (a < 800)  return 2.0
  if (a < 2000) return 1.4
  return 1.0
}

// ── Icons (SVG paths, 24×24 origin) ──────────────────────────────────────────
const ICON = {
  sun: (c:string) => (
    <g>
      <circle cx="0" cy="0" r="5" fill="none" stroke={c} strokeWidth="2"/>
      {[0,45,90,135,180,225,270,315].map(a => {
        const r=a*Math.PI/180, x1=7.5*Math.cos(r), y1=7.5*Math.sin(r)
        return <line key={a} x1={x1} y1={y1} x2={x1*10/7.5} y2={y1*10/7.5} stroke={c} strokeWidth="2" strokeLinecap="round"/>
      })}
    </g>
  ),
  bolt: (c:string) => <polygon points="5,-10 -3,2 3,2 1,10 9,-2 3,-2" fill={c}/>,
  home: (c:string) => (
    <g fill="none" stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="-9,-1 0,-9 9,-1"/>
      <path d="-7,-1 -7,8 -3,8 -3,3 3,3 3,8 7,8 7,-1"/>
    </g>
  ),
}

// ── Component ─────────────────────────────────────────────────────────────────
export default function EnergyFlow({ ppv, pbattery, pgrid, pload, soc }: Props) {
  // Refs to animated paths (for dynamic dur/direction updates)
  const animRefs = useRef<Partial<Record<ArmKey, SVGAnimateElement|null>>>({})

  const pvOn   = ppv      > 30
  const gImp   = pgrid    > 30
  const gExp   = pgrid    < -30
  const bChg   = pbattery > 30
  const bDis   = pbattery < -30
  const hOn    = pload    > 30

  // Per-arm: active, color, reverse (hub→node instead of node→hub)
  const cfg = {
    solar: { on: pvOn,       color: COL.solar,    reverse: false,  power: ppv       },
    grid:  { on: gImp||gExp, color: gExp?COL.grid_exp:COL.grid_imp, reverse: gExp, power: pgrid },
    home:  { on: hOn,        color: COL.home,     reverse: true,   power: pload     },
    bat:   { on: bChg||bDis, color: bChg?COL.bat_chg:COL.bat_dis, reverse: bChg,  power: pbattery },
  } as const satisfies Record<ArmKey, {on:boolean; color:string; reverse:boolean; power:number}>

  // Update SVG animate elements when power changes
  useEffect(() => {
    for (const [key, a] of Object.entries(cfg) as [ArmKey, typeof cfg[ArmKey]][]) {
      const el = animRefs.current[key]
      if (!el) continue
      if (!a.on || period(a.power) === 0) {
        el.setAttribute('dur', '999999s')
        ;(el.parentElement as SVGElement | null)?.setAttribute('stroke-dasharray', '0 999')
        continue
      }
      const dashPeriod = 28  // total dash+gap in SVG units
      ;(el.parentElement as SVGElement | null)?.setAttribute('stroke-dasharray', `10 18`)
      el.setAttribute('dur', `${period(a.power)}s`)
      // reverse=false → dashoffset goes 0→-28 (dashes flow forward along path)
      // reverse=true  → dashoffset goes 0→+28 (dashes flow backward = hub→node)
      el.setAttribute('from', '0')
      el.setAttribute('to',   a.reverse ? `${dashPeriod}` : `-${dashPeriod}`)
    }
  })

  // SoC arc
  const socArc = (() => {
    if (soc < 2) return ''
    const r = NR - 8, bx = NODE.bat.x, by = NODE.bat.y
    const a = (soc / 100) * 2 * Math.PI
    return `M ${bx},${by-r} A ${r},${r} 0 ${soc>50?1:0},1 ${bx+r*Math.sin(a)},${by-r*Math.cos(a)}`
  })()
  const socCol = soc > 60 ? COL.bat_chg : soc > 20 ? COL.solar : '#e06058'

  // ─── Node helper ─────────────────────────────────────────────────────────
  const nodeFill   = (on:boolean, c:string) => on ? `${c}16` : '#09111e'
  const nodeStroke = (on:boolean, c:string) => on ? `${c}80` : '#18283d'

  return (
    <div style={{background:'linear-gradient(160deg,#0c1525 0%,#080d18 100%)',border:'1px solid #18283d',borderRadius:16,padding:'12px 10px 8px'}}>
      <div style={{fontSize:10,fontWeight:700,letterSpacing:'0.12em',color:'#4a637e',textTransform:'uppercase',marginBottom:6,paddingLeft:4}}>
        Energy Flow
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} style={{width:'100%',height:'auto',display:'block',maxHeight:340}}>
        <defs>
          <filter id="glow4" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="4.5" result="b"/>
            <feComposite in="SourceGraphic" in2="b" operator="over"/>
          </filter>
          <filter id="glow8" x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur stdDeviation="9" result="b"/>
            <feComposite in="SourceGraphic" in2="b" operator="over"/>
          </filter>
        </defs>

        {/* ── Animated "marching dash" paths ─────────────────────────── */}
        {(Object.keys(PATHS) as ArmKey[]).map(k => {
          const a = cfg[k]
          return (
            <g key={k}>
              {/* dim background track */}
              <path d={PATHS[k]} fill="none" stroke={a.on ? `${a.color}20` : '#18283d'}
                strokeWidth={a.on?3.5:2} strokeLinecap="round"
                strokeDasharray={a.on?'none':'5 8'}/>
              {/* animated dashes */}
              {a.on && (
                <path d={PATHS[k]} fill="none" stroke={a.color}
                  strokeWidth="3.5" strokeLinecap="round"
                  strokeDasharray="10 18" strokeDashoffset="0"
                  filter="url(#glow4)">
                  <animate ref={el => { animRefs.current[k] = el as SVGAnimateElement | null }}
                    attributeName="stroke-dashoffset"
                    from="0" to="-28"
                    dur={`${period(a.power)}s`}
                    repeatCount="indefinite"
                    calcMode="linear"/>
                </path>
              )}
            </g>
          )
        })}

        {/* ── Hub ──────────────────────────────────────────────────────── */}
        <circle cx={CX} cy={CY} r={10} fill="#0c1830" stroke="#243555" strokeWidth="1.5"/>
        <circle cx={CX} cy={CY} r={5}  fill="#1c3050"/>

        {/* ═══ SOLAR ════════════════════════════════════════════════════ */}
        {pvOn && <circle cx={NODE.solar.x} cy={NODE.solar.y} r={NR+8}
          fill={`${COL.solar}08`} stroke={`${COL.solar}25`} strokeWidth="1" filter="url(#glow8)"/>}
        <circle cx={NODE.solar.x} cy={NODE.solar.y} r={NR}
          fill={nodeFill(pvOn,COL.solar)} stroke={nodeStroke(pvOn,COL.solar)} strokeWidth="2.5"/>
        <g transform={`translate(${NODE.solar.x},${NODE.solar.y - 10})`}>
          {ICON.sun(pvOn ? COL.solar : '#2a3f58')}
        </g>
        <text x={NODE.solar.x} y={NODE.solar.y+16} textAnchor="middle"
          fontSize="13" fontWeight="800" fill={pvOn?'#f0f6ff':'#3a5068'}>{fmt(ppv)}</text>
        <text x={NODE.solar.x} y={NODE.solar.y+NR+16} textAnchor="middle"
          fontSize="11" fill="#506a85">Solar</text>
        {pvOn && <text x={NODE.solar.x} y={NODE.solar.y-NR-8} textAnchor="middle"
          fontSize="10" fill={COL.solar} opacity="0.8">Generating</text>}

        {/* ═══ GRID ═════════════════════════════════════════════════════ */}
        {(gImp||gExp) && <circle cx={NODE.grid.x} cy={NODE.grid.y} r={NR+8}
          fill={`${cfg.grid.color}08`} stroke={`${cfg.grid.color}25`} strokeWidth="1" filter="url(#glow8)"/>}
        <circle cx={NODE.grid.x} cy={NODE.grid.y} r={NR}
          fill={nodeFill(gImp||gExp,cfg.grid.color)} stroke={nodeStroke(gImp||gExp,cfg.grid.color)} strokeWidth="2.5"/>
        <g transform={`translate(${NODE.grid.x},${NODE.grid.y - 6})`}>
          {ICON.bolt(gImp?COL.grid_imp:gExp?COL.grid_exp:'#2a3f58')}
        </g>
        <text x={NODE.grid.x} y={NODE.grid.y+20} textAnchor="middle"
          fontSize="13" fontWeight="800" fill={(gImp||gExp)?'#f0f6ff':'#3a5068'}>{fmt(pgrid)}</text>
        <text x={NODE.grid.x} y={NODE.grid.y-NR-10} textAnchor="middle"
          fontSize="11" fill="#506a85">Grid</text>
        <text x={NODE.grid.x} y={NODE.grid.y+NR+16} textAnchor="middle"
          fontSize="10" fill={gImp?COL.grid_imp:gExp?COL.grid_exp:'#3a5068'}>
          {gImp?'↓ Import':gExp?'↑ Export':'Idle'}
        </text>

        {/* ═══ HOME ═════════════════════════════════════════════════════ */}
        {hOn && <circle cx={NODE.home.x} cy={NODE.home.y} r={NR+8}
          fill={`${COL.home}08`} stroke={`${COL.home}25`} strokeWidth="1" filter="url(#glow8)"/>}
        <circle cx={NODE.home.x} cy={NODE.home.y} r={NR}
          fill={nodeFill(hOn,COL.home)} stroke={nodeStroke(hOn,COL.home)} strokeWidth="2.5"/>
        <g transform={`translate(${NODE.home.x},${NODE.home.y - 6})`}>
          {ICON.home(hOn?COL.home:'#2a3f58')}
        </g>
        <text x={NODE.home.x} y={NODE.home.y+20} textAnchor="middle"
          fontSize="13" fontWeight="800" fill={hOn?'#f0f6ff':'#3a5068'}>{fmt(pload)}</text>
        <text x={NODE.home.x} y={NODE.home.y-NR-10} textAnchor="middle"
          fontSize="11" fill="#506a85">Home</text>

        {/* ═══ BATTERY ══════════════════════════════════════════════════ */}
        {(bChg||bDis) && <circle cx={NODE.bat.x} cy={NODE.bat.y} r={NR+8}
          fill={`${cfg.bat.color}08`} stroke={`${cfg.bat.color}25`} strokeWidth="1" filter="url(#glow8)"/>}
        <circle cx={NODE.bat.x} cy={NODE.bat.y} r={NR}
          fill={nodeFill(bChg||bDis,cfg.bat.color)} stroke={nodeStroke(bChg||bDis,cfg.bat.color)} strokeWidth="2.5"/>
        {soc > 1 && <path d={socArc} fill="none" stroke={socCol} strokeWidth="4.5" strokeLinecap="round"/>}
        <text x={NODE.bat.x} y={NODE.bat.y+8} textAnchor="middle"
          fontSize="16" fontWeight="900" fill={socCol}>{soc}%</text>
        <text x={NODE.bat.x} y={NODE.bat.y+NR+16} textAnchor="middle"
          fontSize="11" fill="#506a85">Battery</text>
        {(bChg||bDis) && <text x={NODE.bat.x} y={NODE.bat.y-NR-8} textAnchor="middle"
          fontSize="10" fill={cfg.bat.color}>
          {bChg?`↑ ${fmt(pbattery)}`:`↓ ${fmt(pbattery)}`}
        </text>}
      </svg>
    </div>
  )
}
