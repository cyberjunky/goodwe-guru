/**
 * EnergyFlow — canvas animation overlay on SVG nodes.
 *
 * Canvas draws tiny dots (r=2.5) traveling along straight-line arms.
 * SVG renders the static nodes and path lines.
 * This is the approach that reliably works in all browsers.
 */
import { useEffect, useRef, useCallback } from 'react'

interface Props {
  ppv:      number   // W
  pbattery: number   // W positive=charging, negative=discharging
  pgrid:    number   // W positive=importing, negative=exporting
  pload:    number   // W
  soc:      number   // %
}

// ── Layout (logical px — canvas + SVG share same coordinate space) ──────────
const W = 440, H = 340
const JX = W / 2, JY = H / 2 - 8    // junction / centre
const NR = 44                         // node circle radius

const NODE = {
  solar: { x: JX,       y: 46   },
  grid:  { x: 62,       y: JY   },
  home:  { x: W - 62,   y: JY   },
  bat:   { x: JX,       y: H - 46 },
}

// Arm endpoints — dots travel FROM → TO when forward (reverse = flip)
const G = NR + 6, HG = 8
const ARMS = {
  solar: { from: { x: JX,             y: NODE.solar.y + G }, to: { x: JX,         y: JY - HG } },
  grid:  { from: { x: NODE.grid.x + G, y: JY               }, to: { x: JX - HG,   y: JY       } },
  home:  { from: { x: JX + HG,         y: JY               }, to: { x: NODE.home.x - G, y: JY  } },
  bat:   { from: { x: JX,              y: JY + HG          }, to: { x: JX,         y: NODE.bat.y - G } },
} as const
type ArmKey = keyof typeof ARMS

// ── Colours ─────────────────────────────────────────────────────────────────
const C = {
  solar:   '#f59e0b',
  grid_imp:'#f87171',
  grid_exp:'#34d399',
  home:    '#a78bfa',
  bat_chg: '#34d399',
  bat_dis: '#fb923c',
}

// ── Animation math ──────────────────────────────────────────────────────────
function speed(w: number) {           // dots/sec (higher = faster)
  const a = Math.abs(w)
  if (a <   50) return 0
  if (a <  300) return 0.15
  if (a <  800) return 0.22
  if (a < 2000) return 0.32
  return 0.45
}
function count(w: number) {
  const a = Math.abs(w)
  if (a <   50) return 0
  if (a <  400) return 2
  if (a < 1500) return 3
  return 4
}

interface Dot {
  arm:     ArmKey
  t:       number   // 0-1 along arm
  reverse: boolean
  color:   string
  spd:     number
}

function lerp(a: { x: number; y: number }, b: { x: number; y: number }, t: number) {
  return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }
}

// ── Helpers ─────────────────────────────────────────────────────────────────
const fmt = (w: number) => {
  const a = Math.abs(w)
  return a >= 1000 ? `${(a / 1000).toFixed(2)} kW` : `${Math.round(a)} W`
}

// ── Sun icon (24×24 centred at 0,0) ─────────────────────────────────────────
function SunIcon({ c }: { c: string }) {
  return (
    <g stroke={c} strokeWidth="1.8" strokeLinecap="round" fill="none">
      <circle cx="0" cy="0" r="4.8" />
      {[0, 45, 90, 135, 180, 225, 270, 315].map(a => {
        const r = a * Math.PI / 180
        return <line key={a}
          x1={6.8 * Math.cos(r)} y1={6.8 * Math.sin(r)}
          x2={9.2 * Math.cos(r)} y2={9.2 * Math.sin(r)} />
      })}
    </g>
  )
}
function BoltIcon({ c }: { c: string }) {
  return <polygon points="4.5,-9 -3.5,1 2.5,1 -1.5,9 7.5,-1 1.5,-1" fill={c} />
}
function HomeIcon({ c }: { c: string }) {
  return (
    <g stroke={c} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" fill="none">
      <polyline points="-9,-1 0,-8.5 9,-1" />
      <path d="-6.5,-1 -6.5,8 -3,8 -3,2.5 3,2.5 3,8 6.5,8 6.5,-1" />
    </g>
  )
}

// ── Component ────────────────────────────────────────────────────────────────
export default function EnergyFlow({ ppv, pbattery, pgrid, pload, soc }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const dotsRef   = useRef<Dot[]>([])
  const frameRef  = useRef<number>(0)
  const lastRef   = useRef<number>(0)
  const propsRef  = useRef({ ppv, pbattery, pgrid, pload })

  // Keep latest props accessible inside rAF without closure issues
  propsRef.current = { ppv, pbattery, pgrid, pload }

  // Rebuild dot list whenever power changes meaningfully
  useEffect(() => {
    const pvOn  = ppv      > 30
    const gImp  = pgrid    > 30
    const gExp  = pgrid    < -30
    const bChg  = pbattery > 30
    const bDis  = pbattery < -30
    const hOn   = pload    > 30

    const wanted: { arm: ArmKey; reverse: boolean; color: string; w: number }[] = [
      { arm: 'solar', reverse: false, color: C.solar,    w: pvOn            ? ppv      : 0 },
      { arm: 'grid',  reverse: gExp,  color: gExp ? C.grid_exp : C.grid_imp, w: (gImp||gExp) ? pgrid : 0 },
      { arm: 'home',  reverse: false, color: C.home,     w: hOn             ? pload    : 0 },
      // bat arm goes FROM junction TO battery, so forward = charging (junction→bat)
      // discharging = reverse (bat→junction)
      { arm: 'bat',   reverse: bDis,  color: bChg ? C.bat_chg : C.bat_dis,  w: (bChg||bDis) ? pbattery : 0 },
    ]

    const next: Dot[] = []
    wanted.forEach(({ arm, reverse, color, w }) => {
      const n = count(w)
      const s = speed(w)
      for (let i = 0; i < n; i++) {
        const existing = dotsRef.current.find(d => d.arm === arm && d.reverse === reverse)
        next.push({
          arm, reverse, color, spd: s,
          t: existing ? existing.t : i / Math.max(n, 1),
        })
      }
    })
    dotsRef.current = next
  }, [ppv, pbattery, pgrid, pload])

  // rAF draw loop
  const draw = useCallback((now: number) => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')!
    const dt  = Math.min((now - (lastRef.current || now)) / 1000, 0.05)
    lastRef.current = now

    const dpr = window.devicePixelRatio || 1
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    ctx.save()
    ctx.scale(dpr, dpr)

    // Advance and draw each dot
    for (const dot of dotsRef.current) {
      dot.t = (dot.t + dot.spd * dt) % 1
      const arm = ARMS[dot.arm]
      const t   = dot.reverse ? 1 - dot.t : dot.t
      const pos = lerp(arm.from, arm.to, t)

      // Outer glow
      const grd = ctx.createRadialGradient(pos.x, pos.y, 0, pos.x, pos.y, 7)
      grd.addColorStop(0, dot.color + 'aa')
      grd.addColorStop(1, dot.color + '00')
      ctx.beginPath()
      ctx.arc(pos.x, pos.y, 7, 0, Math.PI * 2)
      ctx.fillStyle = grd
      ctx.fill()

      // Dot core
      ctx.beginPath()
      ctx.arc(pos.x, pos.y, 2.5, 0, Math.PI * 2)
      ctx.fillStyle = dot.color
      ctx.fill()

      // Bright centre
      ctx.beginPath()
      ctx.arc(pos.x, pos.y, 1, 0, Math.PI * 2)
      ctx.fillStyle = '#ffffff'
      ctx.fill()
    }

    ctx.restore()
    frameRef.current = requestAnimationFrame(draw)
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current!
    const dpr = window.devicePixelRatio || 1
    canvas.width  = W * dpr
    canvas.height = H * dpr
    canvas.style.width  = W + 'px'
    canvas.style.height = H + 'px'
    frameRef.current = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(frameRef.current)
  }, [draw])

  // Derived display states
  const pvOn = ppv      > 30
  const gImp = pgrid    > 30
  const gExp = pgrid    < -30
  const bChg = pbattery > 30
  const bDis = pbattery < -30

  const gridColor = gImp ? C.grid_imp : gExp ? C.grid_exp : '#1e3050'
  const batColor  = bChg ? C.bat_chg  : bDis ? C.bat_dis  : '#1e3050'

  const socArc = (() => {
    if (soc < 2) return ''
    const r = NR - 8, bx = NODE.bat.x, by = NODE.bat.y
    const a = (soc / 100) * 2 * Math.PI
    return `M ${bx},${by - r} A ${r},${r} 0 ${soc > 50 ? 1 : 0},1 ${bx + r * Math.sin(a)},${by - r * Math.cos(a)}`
  })()
  const socCol = soc > 60 ? '#34d399' : soc > 20 ? '#f59e0b' : '#f87171'

  const nFill   = (on: boolean, c: string) => on ? `${c}18` : '#08111e'
  const nStroke = (on: boolean, c: string) => on ? c : '#1e3050'
  const lStroke = (on: boolean, c: string) => on ? c + '90' : '#1e3050'

  return (
    <div style={{ background: 'linear-gradient(160deg,#0b1828 0%,#07101a 100%)',
      border: '1px solid #18283d', borderRadius: 14, padding: '10px 6px 6px' }}>
      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.13em',
        textTransform: 'uppercase', color: '#3a5770', marginBottom: 2, paddingLeft: 6 }}>
        Energy Flow
      </div>

      <div style={{ position: 'relative', width: W, height: H, maxWidth: '100%' }}>
        {/* Canvas: animated dots */}
        <canvas ref={canvasRef}
          style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none' }} />

        {/* SVG: static lines + nodes */}
        <svg viewBox={`0 0 ${W} ${H}`}
          style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%' }}>

          {/* Arm lines */}
          <line x1={ARMS.solar.from.x} y1={ARMS.solar.from.y}
            x2={ARMS.solar.to.x}   y2={ARMS.solar.to.y}
            stroke={lStroke(pvOn, C.solar)} strokeWidth="2"
            strokeDasharray={pvOn ? 'none' : '5 7'} strokeLinecap="round" opacity={pvOn ? 1 : 0.35} />

          <line x1={ARMS.grid.from.x} y1={ARMS.grid.from.y}
            x2={ARMS.grid.to.x}   y2={ARMS.grid.to.y}
            stroke={lStroke(gImp || gExp, gridColor)} strokeWidth="2"
            strokeDasharray={(gImp || gExp) ? 'none' : '5 7'} strokeLinecap="round"
            opacity={(gImp || gExp) ? 1 : 0.35} />

          <line x1={ARMS.home.from.x} y1={ARMS.home.from.y}
            x2={ARMS.home.to.x}   y2={ARMS.home.to.y}
            stroke={lStroke(pvOn || gImp || bDis, C.home)} strokeWidth="2"
            strokeDasharray={(pvOn || gImp || bDis) ? 'none' : '5 7'} strokeLinecap="round"
            opacity={(pvOn || gImp || bDis) ? 1 : 0.35} />

          <line x1={ARMS.bat.from.x} y1={ARMS.bat.from.y}
            x2={ARMS.bat.to.x}   y2={ARMS.bat.to.y}
            stroke={lStroke(bChg || bDis, batColor)} strokeWidth="2"
            strokeDasharray={(bChg || bDis) ? 'none' : '5 7'} strokeLinecap="round"
            opacity={(bChg || bDis) ? 1 : 0.35} />

          {/* Junction */}
          <circle cx={JX} cy={JY} r="8" fill="#08111e" stroke="#1e3050" strokeWidth="1.5" />
          <circle cx={JX} cy={JY} r="4" fill="#172840" />

          {/* ── Solar ──────────────────────────────────── */}
          <circle cx={NODE.solar.x} cy={NODE.solar.y} r={NR}
            fill={nFill(pvOn, C.solar)} stroke={nStroke(pvOn, C.solar)} strokeWidth={pvOn ? 2 : 1.5} />
          <g transform={`translate(${NODE.solar.x},${NODE.solar.y - 10})`}>
            <SunIcon c={pvOn ? C.solar : '#2a3f55'} />
          </g>
          <text x={NODE.solar.x} y={NODE.solar.y + 15} textAnchor="middle"
            fontSize="12" fontWeight="700" fill={pvOn ? '#eef4ff' : '#2a3f55'}
            fontFamily="system-ui,sans-serif">{fmt(ppv)}</text>
          <text x={NODE.solar.x} y={NODE.solar.y + NR + 16} textAnchor="middle"
            fontSize="10" fill="#4a6a85" fontFamily="system-ui,sans-serif">Solar</text>

          {/* ── Grid ───────────────────────────────────── */}
          <circle cx={NODE.grid.x} cy={NODE.grid.y} r={NR}
            fill={nFill(gImp || gExp, gridColor)} stroke={nStroke(gImp || gExp, gridColor)}
            strokeWidth={(gImp || gExp) ? 2 : 1.5} />
          <g transform={`translate(${NODE.grid.x},${NODE.grid.y - 6})`}>
            <BoltIcon c={(gImp || gExp) ? gridColor : '#2a3f55'} />
          </g>
          <text x={NODE.grid.x} y={NODE.grid.y + 20} textAnchor="middle"
            fontSize="12" fontWeight="700" fill={(gImp || gExp) ? '#eef4ff' : '#2a3f55'}
            fontFamily="system-ui,sans-serif">{fmt(pgrid)}</text>
          <text x={NODE.grid.x} y={NODE.grid.y - NR - 10} textAnchor="middle"
            fontSize="10" fill="#4a6a85" fontFamily="system-ui,sans-serif">Grid</text>
          <text x={NODE.grid.x} y={NODE.grid.y + NR + 16} textAnchor="middle"
            fontSize="9.5" fill={gImp ? C.grid_imp : gExp ? C.grid_exp : '#2a3f55'}
            fontFamily="system-ui,sans-serif">
            {gImp ? '↓ Import' : gExp ? '↑ Export' : 'Idle'}
          </text>

          {/* ── Home ───────────────────────────────────── */}
          <circle cx={NODE.home.x} cy={NODE.home.y} r={NR}
            fill={nFill(true, C.home)} stroke={nStroke(pvOn || gImp || bDis, C.home)}
            strokeWidth={(pvOn || gImp || bDis) ? 2 : 1.5} />
          <g transform={`translate(${NODE.home.x},${NODE.home.y - 6})`}>
            <HomeIcon c={C.home} />
          </g>
          <text x={NODE.home.x} y={NODE.home.y + 20} textAnchor="middle"
            fontSize="12" fontWeight="700" fill="#eef4ff"
            fontFamily="system-ui,sans-serif">{fmt(pload)}</text>
          <text x={NODE.home.x} y={NODE.home.y - NR - 10} textAnchor="middle"
            fontSize="10" fill="#4a6a85" fontFamily="system-ui,sans-serif">Home</text>

          {/* ── Battery ────────────────────────────────── */}
          <circle cx={NODE.bat.x} cy={NODE.bat.y} r={NR}
            fill={nFill(bChg || bDis, batColor)} stroke={nStroke(bChg || bDis, batColor)}
            strokeWidth={(bChg || bDis) ? 2 : 1.5} />
          {soc > 1 && (
            <path d={socArc} fill="none" stroke={socCol} strokeWidth="4" strokeLinecap="round" />
          )}
          <text x={NODE.bat.x} y={NODE.bat.y + 7} textAnchor="middle"
            fontSize="15" fontWeight="800" fill={socCol}
            fontFamily="system-ui,sans-serif">{soc}%</text>
          <text x={NODE.bat.x} y={NODE.bat.y + NR + 16} textAnchor="middle"
            fontSize="10" fill="#4a6a85" fontFamily="system-ui,sans-serif">Battery</text>
          {(bChg || bDis) && (
            <text x={NODE.bat.x} y={NODE.bat.y - NR - 8} textAnchor="middle"
              fontSize="9.5" fill={batColor} fontFamily="system-ui,sans-serif">
              {bChg ? `↑ ${fmt(pbattery)}` : `↓ ${fmt(pbattery)}`}
            </text>
          )}
        </svg>
      </div>
    </div>
  )
}
