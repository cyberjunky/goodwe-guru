/**
 * EnergyFlow — HA power-flow-card style animation.
 *
 * Layout: Solar (top) — Grid (left) — Home (right) — Battery (bottom)
 * all meeting at a central junction.  Paths are straight lines.
 * Tiny dots (r=2) travel along each arm using SVG animateMotion + mpath.
 * Speed scales with power; direction via keyPoints="1;0" for reverse arms.
 *
 * Inspired by https://github.com/ulic75/power-flow-card
 */

import { useEffect, useRef } from 'react'

// ─── Props ─────────────────────────────────────────────────────────────────
interface Props {
  ppv:      number  // W – solar (≥0)
  pbattery: number  // W – positive=charging, negative=discharging
  pgrid:    number  // W – positive=importing, negative=exporting
  pload:    number  // W – load
  soc:      number  // %
}

// ─── Layout (viewBox 0 0 420 340) ──────────────────────────────────────────
const VW = 420, VH = 340
const JX = VW / 2, JY = VH / 2 - 10   // junction point (centre)
const NR = 44                           // node radius

const NODE = {
  solar: { x: JX,        y: 46          },
  grid:  { x: 64,        y: JY          },
  home:  { x: VW - 64,   y: JY          },
  bat:   { x: JX,        y: VH - 46     },
}

// Paths: defined so t=0 is at the satellite node, t=1 is at the junction.
// Direction on each arm:
//   solar  → junction  : always (generation flows to inverter)
//   grid   → junction  : import   (pgrid>0)
//   junction → grid    : export   (pgrid<0) → keyPoints="1;0"
//   junction → home    : always   (inverter → load)   → keyPoints="1;0" on arm
//   junction → battery : charging (pbattery>0) → keyPoints="1;0"
//   battery → junction : discharging (pbattery<0)
//
// Note: for "home arm" the path is defined junction→home (t=0 at junction)
// so "forward" already means junction→home = correct, no reversal needed.

const GAP = NR + 4    // gap from node edge to start of path
const HG  = 6         // gap from junction to end of path

const PATH_D = {
  solar: `M ${JX},${NODE.solar.y + GAP} L ${JX},${JY - HG}`,
  grid:  `M ${NODE.grid.x + GAP},${JY} L ${JX - HG},${JY}`,
  home:  `M ${JX + HG},${JY} L ${NODE.home.x - GAP},${JY}`,
  bat:   `M ${JX},${JY + HG} L ${JX},${NODE.bat.y - GAP}`,
} as const
type ArmKey = keyof typeof PATH_D

// ─── Colours ───────────────────────────────────────────────────────────────
const COL: Record<ArmKey | 'bat_chg' | 'bat_dis' | 'g_imp' | 'g_exp', string> = {
  solar:   '#f59e0b',
  grid:    '#60a5fa',
  home:    '#a78bfa',
  bat:     '#34d399',
  bat_chg: '#34d399',
  bat_dis: '#fb923c',
  g_imp:   '#f87171',
  g_exp:   '#34d399',
}

// ─── Animation helpers ──────────────────────────────────────────────────────
const DUR_MIN = 0.5, DUR_MAX = 5.5, WATT_MAX = 5000

function calcDur(w: number): number {
  const a = Math.min(Math.abs(w), WATT_MAX)
  return DUR_MAX - (a / WATT_MAX) * (DUR_MAX - DUR_MIN)
}

function dotCount(w: number): number {
  const a = Math.abs(w)
  if (a <   50) return 0
  if (a <  400) return 2
  if (a < 1200) return 3
  return 4
}

const MAX_DOTS = 4

const fmt = (w: number) => {
  const a = Math.abs(w)
  return a >= 1000 ? `${(a / 1000).toFixed(2)} kW` : `${Math.round(a)} W`
}

// ─── Icons (24×24 SVG paths, rendered at (0,0)) ────────────────────────────
const SunIcon = ({ c }: { c: string }) => (
  <g stroke={c} strokeWidth="1.8" strokeLinecap="round" fill="none">
    <circle cx="0" cy="0" r="4.5"/>
    {[0,45,90,135,180,225,270,315].map(a => {
      const r = a * Math.PI / 180
      return <line key={a}
        x1={6.5 * Math.cos(r)} y1={6.5 * Math.sin(r)}
        x2={9   * Math.cos(r)} y2={9   * Math.sin(r)}/>
    })}
  </g>
)

const BoltIcon = ({ c }: { c: string }) => (
  <polygon points="4,-9 -3,1 2,1 -1,9 7,-1 2,-1" fill={c}/>
)

const HomeIcon = ({ c }: { c: string }) => (
  <g stroke={c} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" fill="none">
    <polyline points="-8.5,-1 0,-8.5 8.5,-1"/>
    <path d="-6,-1 -6,7.5 -2.5,7.5 -2.5,2.5 2.5,2.5 2.5,7.5 6,7.5 6,-1"/>
  </g>
)

// ─── Component ─────────────────────────────────────────────────────────────
export default function EnergyFlow({ ppv, pbattery, pgrid, pload, soc }: Props) {

  // refs: animateMotion elements per arm (MAX_DOTS each)
  type AMRef = SVGAnimateMotionElement | null
  const amRefs = useRef<Record<ArmKey, AMRef[]>>({
    solar: Array(MAX_DOTS).fill(null),
    grid:  Array(MAX_DOTS).fill(null),
    home:  Array(MAX_DOTS).fill(null),
    bat:   Array(MAX_DOTS).fill(null),
  })

  const pvOn  = ppv      > 30
  const gImp  = pgrid    > 30
  const gExp  = pgrid    < -30
  const bChg  = pbattery > 30
  const bDis  = pbattery < -30
  const hOn   = pload    > 30

  const armCfg = {
    solar: { power: ppv,      active: pvOn,        reverse: false, color: COL.solar                          },
    grid:  { power: pgrid,    active: gImp || gExp, reverse: gExp, color: gExp ? COL.g_exp : COL.g_imp       },
    home:  { power: pload,    active: hOn,         reverse: false, color: COL.home                           },
    bat:   { power: pbattery, active: bChg || bDis, reverse: bChg, color: bChg ? COL.bat_chg : COL.bat_dis  },
  } satisfies Record<ArmKey, { power: number; active: boolean; reverse: boolean; color: string }>

  // Update animateMotion attributes reactively
  useEffect(() => {
    for (const [key, cfg] of Object.entries(armCfg) as [ArmKey, typeof armCfg[ArmKey]][]) {
      const els = amRefs.current[key]
      const count = cfg.active ? dotCount(cfg.power) : 0
      const dur   = calcDur(cfg.power)
      const kp    = cfg.reverse ? '1;0' : '0;1'

      els.forEach((el, i) => {
        const wrapper = el?.parentElement as SVGElement | null
        if (!wrapper) return
        if (i >= count) { wrapper.setAttribute('display', 'none'); return }
        wrapper.removeAttribute('display')
        el!.setAttribute('dur',        `${dur.toFixed(3)}s`)
        el!.setAttribute('keyPoints',  kp)
        el!.setAttribute('keyTimes',   '0;1')
        el!.setAttribute('calcMode',   'linear')
        el!.setAttribute('repeatCount','indefinite')
        // stagger: evenly distribute dots along the path
        el!.setAttribute('begin', i === 0 ? '0s' : `-${(dur * i / count).toFixed(3)}s`)
      })
    }
  })

  // SoC arc for battery node
  const socArc = (() => {
    if (soc < 2) return ''
    const r = NR - 8, bx = NODE.bat.x, by = NODE.bat.y
    const a = (soc / 100) * 2 * Math.PI
    return `M ${bx},${by - r} A ${r},${r} 0 ${soc > 50 ? 1 : 0},1 ${bx + r * Math.sin(a)},${by - r * Math.cos(a)}`
  })()
  const socCol = soc > 60 ? '#34d399' : soc > 20 ? '#f59e0b' : '#f87171'

  // Junction lines (the cross through the centre)
  const J_LEN = 5

  function setAMRef(key: ArmKey, i: number) {
    return (el: SVGAnimateMotionElement | null) => { amRefs.current[key][i] = el }
  }

  const nodeFill   = (on: boolean, c: string) => on ? `${c}18` : '#09111e'
  const nodeStroke = (on: boolean, c: string) => on ? c         : '#1e2d42'
  const pathStroke = (cfg: typeof armCfg[ArmKey]) =>
    cfg.active ? cfg.color : '#1e2d42'

  return (
    <div style={{
      background: 'linear-gradient(160deg,#0b1828 0%,#070e1a 100%)',
      border: '1px solid #18283d',
      borderRadius: 14,
      padding: '12px 8px 8px',
    }}>
      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.13em',
        textTransform: 'uppercase', color: '#3d5670', marginBottom: 4, paddingLeft: 6 }}>
        Energy Flow
      </div>

      <svg viewBox={`0 0 ${VW} ${VH}`} style={{ width: '100%', height: 'auto', display: 'block', maxHeight: 320 }}>

        {/* Hidden path definitions for mpath references */}
        <defs>
          {(Object.entries(PATH_D) as [ArmKey, string][]).map(([k, d]) => (
            <path key={k} id={`efp-${k}`} d={d}/>
          ))}
        </defs>

        {/* ── Arm lines ─────────────────────────────────────────────── */}
        {(Object.entries(PATH_D) as [ArmKey, string][]).map(([k, d]) => {
          const cfg = armCfg[k]
          return (
            <path key={k} d={d} fill="none"
              stroke={pathStroke(cfg)} strokeWidth="2.5"
              strokeDasharray={cfg.active ? 'none' : '5 6'}
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
              opacity={cfg.active ? 0.75 : 0.3}/>
          )
        })}

        {/* ── Animated dots (MAX_DOTS per arm) ─────────────────────── */}
        {(Object.keys(PATH_D) as ArmKey[]).map(k =>
          Array.from({ length: MAX_DOTS }, (_, i) => (
            <g key={`${k}-${i}`} display="none">
              <circle r="2" fill={armCfg[k].color} opacity="0.95">
                <animateMotion ref={setAMRef(k, i)}
                  dur="2s" repeatCount="indefinite" calcMode="linear"
                  keyPoints="0;1" keyTimes="0;1">
                  <mpath href={`#efp-${k}`}/>
                </animateMotion>
              </circle>
            </g>
          ))
        )}

        {/* ── Junction cross ────────────────────────────────────────── */}
        <circle cx={JX} cy={JY} r={J_LEN + 2} fill="#0b1828" stroke="#243654" strokeWidth="1.5"/>
        <circle cx={JX} cy={JY} r={J_LEN - 1} fill="#18304a"/>

        {/* ═══ SOLAR NODE ═════════════════════════════════════════════ */}
        <circle cx={NODE.solar.x} cy={NODE.solar.y} r={NR}
          fill={nodeFill(pvOn, COL.solar)} stroke={nodeStroke(pvOn, COL.solar)}
          strokeWidth={pvOn ? 2 : 1.5}/>
        <g transform={`translate(${NODE.solar.x},${NODE.solar.y - 9})`}>
          <SunIcon c={pvOn ? COL.solar : '#2d4255'}/>
        </g>
        <text x={NODE.solar.x} y={NODE.solar.y + 15} textAnchor="middle"
          fontSize="12" fontWeight="700" fill={pvOn ? '#f0f6ff' : '#2d4255'}
          fontFamily="system-ui,sans-serif">{fmt(ppv)}</text>
        <text x={NODE.solar.x} y={NODE.solar.y + NR + 16} textAnchor="middle"
          fontSize="10.5" fill="#4a6a8a" fontFamily="system-ui,sans-serif">Solar</text>
        {pvOn && (
          <text x={NODE.solar.x} y={NODE.solar.y - NR - 8} textAnchor="middle"
            fontSize="9.5" fill={COL.solar} fontFamily="system-ui,sans-serif">Generating</text>
        )}

        {/* ═══ GRID NODE ══════════════════════════════════════════════ */}
        <circle cx={NODE.grid.x} cy={NODE.grid.y} r={NR}
          fill={nodeFill(gImp || gExp, armCfg.grid.color)} stroke={nodeStroke(gImp || gExp, armCfg.grid.color)}
          strokeWidth={(gImp || gExp) ? 2 : 1.5}/>
        <g transform={`translate(${NODE.grid.x},${NODE.grid.y - 5})`}>
          <BoltIcon c={(gImp || gExp) ? armCfg.grid.color : '#2d4255'}/>
        </g>
        <text x={NODE.grid.x} y={NODE.grid.y + 19} textAnchor="middle"
          fontSize="12" fontWeight="700" fill={(gImp || gExp) ? '#f0f6ff' : '#2d4255'}
          fontFamily="system-ui,sans-serif">{fmt(pgrid)}</text>
        <text x={NODE.grid.x} y={NODE.grid.y - NR - 10} textAnchor="middle"
          fontSize="10.5" fill="#4a6a8a" fontFamily="system-ui,sans-serif">Grid</text>
        <text x={NODE.grid.x} y={NODE.grid.y + NR + 16} textAnchor="middle"
          fontSize="9.5" fill={gImp ? COL.g_imp : gExp ? COL.g_exp : '#2d4255'}
          fontFamily="system-ui,sans-serif">
          {gImp ? '↓ Import' : gExp ? '↑ Export' : 'Idle'}
        </text>

        {/* ═══ HOME NODE ══════════════════════════════════════════════ */}
        <circle cx={NODE.home.x} cy={NODE.home.y} r={NR}
          fill={nodeFill(hOn, COL.home)} stroke={nodeStroke(hOn, COL.home)}
          strokeWidth={hOn ? 2 : 1.5}/>
        <g transform={`translate(${NODE.home.x},${NODE.home.y - 5})`}>
          <HomeIcon c={hOn ? COL.home : '#2d4255'}/>
        </g>
        <text x={NODE.home.x} y={NODE.home.y + 19} textAnchor="middle"
          fontSize="12" fontWeight="700" fill={hOn ? '#f0f6ff' : '#2d4255'}
          fontFamily="system-ui,sans-serif">{fmt(pload)}</text>
        <text x={NODE.home.x} y={NODE.home.y - NR - 10} textAnchor="middle"
          fontSize="10.5" fill="#4a6a8a" fontFamily="system-ui,sans-serif">Home</text>

        {/* ═══ BATTERY NODE ═══════════════════════════════════════════ */}
        <circle cx={NODE.bat.x} cy={NODE.bat.y} r={NR}
          fill={nodeFill(bChg || bDis, armCfg.bat.color)} stroke={nodeStroke(bChg || bDis, armCfg.bat.color)}
          strokeWidth={(bChg || bDis) ? 2 : 1.5}/>
        {soc > 1 && (
          <path d={socArc} fill="none" stroke={socCol} strokeWidth="4" strokeLinecap="round"/>
        )}
        <text x={NODE.bat.x} y={NODE.bat.y + 6} textAnchor="middle"
          fontSize="15" fontWeight="800" fill={socCol}
          fontFamily="system-ui,sans-serif">{soc}%</text>
        <text x={NODE.bat.x} y={NODE.bat.y + NR + 16} textAnchor="middle"
          fontSize="10.5" fill="#4a6a8a" fontFamily="system-ui,sans-serif">Battery</text>
        {(bChg || bDis) && (
          <text x={NODE.bat.x} y={NODE.bat.y - NR - 8} textAnchor="middle"
            fontSize="9.5" fill={armCfg.bat.color} fontFamily="system-ui,sans-serif">
            {bChg ? `↑ ${fmt(pbattery)}` : `↓ ${fmt(pbattery)}`}
          </text>
        )}
      </svg>
    </div>
  )
}
