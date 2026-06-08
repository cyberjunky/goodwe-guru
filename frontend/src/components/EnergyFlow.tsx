/**
 * EnergyFlow — animated energy diagram.
 *
 * Layout (SVG viewBox 440×440, scales to container width):
 *
 *           ☀ Solar  (top-center)
 *               |
 *  ⚡ Grid ─── ● ─── 🏠 Home
 *               |
 *           🔋 Battery (bottom-center)
 *
 * SVG paths + JS rAF loop for 60 fps dot animation.
 * Dot speed & count scale with power; direction shows flow direction.
 */

import { useEffect, useRef } from 'react'

interface Props {
  ppv:      number   // W – solar (≥ 0)
  pbattery: number   // W – positive = charging, negative = discharging
  pgrid:    number   // W – positive = importing, negative = exporting
  pload:    number   // W – home consumption
  soc:      number   // %
}

// ─────────────────────────────────────────────────────────────────────────────
// Layout constants (in SVG user units, viewBox 0 0 440 440)
// ─────────────────────────────────────────────────────────────────────────────
const VB   = 440
const CX   = VB / 2   // 220 — hub x
const CY   = VB / 2   // 220 — hub y
const NR   = 44       // node radius
const HR   = 9        // hub radius

const POS = {
  solar:   { x: CX,          y: 52  },
  grid:    { x: 52,          y: CY  },
  home:    { x: VB - 52,     y: CY  },
  battery: { x: CX,          y: VB - 52 },
  hub:     { x: CX,          y: CY  },
}

// Path d attributes (quadratic bezier for slight organic curve)
// Each arm connects node edge → hub edge
const GAP = NR + 6
const HG  = HR + 4

const PATHS = {
  solar: `M ${CX} ${POS.solar.y   + GAP} Q ${CX - 14} ${CY - 40} ${CX} ${CY - HG}`,
  grid:  `M ${POS.grid.x  + GAP} ${CY} Q ${CX - 40} ${CY - 14} ${CX - HG} ${CY}`,
  home:  `M ${POS.home.x  - GAP} ${CY} Q ${CX + 40} ${CY + 14} ${CX + HG} ${CY}`,
  bat:   `M ${CX} ${POS.battery.y - GAP} Q ${CX + 14} ${CY + 40} ${CX} ${CY + HG}`,
} as const

type ArmKey = keyof typeof PATHS

const COLORS: Record<ArmKey, string> = {
  solar: '#f0a820',   // warm gold
  grid:  '#5590e0',   // steel blue
  home:  '#9265d4',   // soft purple
  bat:   '#00c49a',   // teal-green
}

// ─────────────────────────────────────────────────────────────────────────────
// Helper – label with power
// ─────────────────────────────────────────────────────────────────────────────
function fmtW(w: number) {
  const a = Math.abs(w)
  return a >= 1000 ? `${(a / 1000).toFixed(2)} kW` : `${Math.round(a)} W`
}

// ─────────────────────────────────────────────────────────────────────────────
// Dot config from power
// ─────────────────────────────────────────────────────────────────────────────
const MAX_DOTS = 5   // per arm

function dotParams(watts: number): { count: number; period: number } {
  const a = Math.abs(watts)
  if (a <   30) return { count: 0, period: 0 }
  if (a <  400) return { count: 1, period: 2.6 }
  if (a < 1200) return { count: 2, period: 2.2 }
  if (a < 2500) return { count: 3, period: 1.8 }
  if (a < 4000) return { count: 4, period: 1.4 }
  return             { count: 5, period: 1.1 }
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────
interface DotSpec {
  arm: ArmKey
  offset: number    // 0-1 stagger offset
  period: number    // seconds for full traversal
  reverse: boolean
  color: string
}

export default function EnergyFlow({ ppv, pbattery, pgrid, pload, soc }: Props) {
  // Refs to the 4 SVG path elements (for getTotalLength / getPointAtLength)
  const pathRefs = useRef<Partial<Record<ArmKey, SVGPathElement | null>>>({})
  // Flat array of dot <circle> DOM elements (MAX_DOTS × 4 arms)
  const dotEls   = useRef<(SVGCircleElement | null)[]>(Array(MAX_DOTS * 4).fill(null))
  // Dot positions (t: 0-1 along arm)
  const dotsT    = useRef<number[]>(Array(MAX_DOTS * 4).fill(0))
  const specsRef = useRef<DotSpec[]>([])
  const frameRef = useRef<number>(0)
  const lastRef  = useRef<number>(0)

  // ── Build dot spec list when power changes ────────────────────────────────
  useEffect(() => {
    const arms: { arm: ArmKey; watts: number; reverse: boolean }[] = [
      { arm: 'solar', watts: ppv,                   reverse: false },
      { arm: 'grid',  watts: pgrid,                 reverse: pgrid < 0 },
      { arm: 'home',  watts: pload,                 reverse: true  },
      { arm: 'bat',   watts: pbattery,              reverse: pbattery > 0 },
    ]

    const next: DotSpec[] = []
    arms.forEach(({ arm, watts, reverse }) => {
      const { count, period } = dotParams(watts)
      for (let i = 0; i < count; i++) {
        next.push({ arm, offset: i / Math.max(count, 1), period, reverse, color: COLORS[arm] })
      }
    })
    specsRef.current = next
  }, [ppv, pbattery, pgrid, pload])

  // ── Animation loop ────────────────────────────────────────────────────────
  useEffect(() => {
    function tick(now: number) {
      const dt = Math.min((now - (lastRef.current || now)) / 1000, 0.05)
      lastRef.current = now

      const specs = specsRef.current

      // Hide all dots first
      dotEls.current.forEach(el => { if (el) el.style.opacity = '0' })

      specs.forEach((spec, si) => {
        if (si >= dotEls.current.length) return
        const el   = dotEls.current[si]
        const path = pathRefs.current[spec.arm]
        if (!el || !path) return

        // Advance t
        dotsT.current[si] = ((dotsT.current[si] ?? spec.offset) + dt / spec.period) % 1

        const t      = (dotsT.current[si] + spec.offset) % 1
        const tFinal = spec.reverse ? 1 - t : t
        const len    = path.getTotalLength()
        const pt     = path.getPointAtLength(tFinal * len)

        el.setAttribute('cx', pt.x.toString())
        el.setAttribute('cy', pt.y.toString())
        el.style.opacity = '1'
      })

      frameRef.current = requestAnimationFrame(tick)
    }

    frameRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frameRef.current)
  }, [])

  // ── Derived states ────────────────────────────────────────────────────────
  const pvOn     = ppv      >  30
  const gridImp  = pgrid    >  30
  const gridExp  = pgrid    < -30
  const batChg   = pbattery >  30
  const batDis   = pbattery < -30

  // SoC arc path for battery node
  const socArc = (() => {
    if (soc <= 0) return ''
    const r    = NR - 6
    const bx   = POS.battery.x, by = POS.battery.y
    const start = { x: bx, y: by - r }        // top of circle
    const end = {
      x: bx + r * Math.sin((soc / 100) * 2 * Math.PI),
      y: by - r * Math.cos((soc / 100) * 2 * Math.PI),
    }
    const large = soc > 50 ? 1 : 0
    return `M ${start.x},${start.y} A ${r},${r} 0 ${large},1 ${end.x},${end.y}`
  })()

  const socColor = soc > 60 ? '#00c49a' : soc > 20 ? '#f0a820' : '#e06058'

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="rounded-2xl p-4" style={{ background: 'linear-gradient(145deg, #0e1829 0%, #0a1322 100%)', border: '1px solid #1a2a44' }}>
      <h2 className="text-[11px] font-semibold text-gray-500 uppercase tracking-widest mb-2 px-1">Energy Flow</h2>

      <svg
        viewBox={`0 0 ${VB} ${VB}`}
        className="w-full max-w-sm sm:max-w-md mx-auto block"
        style={{ height: 'auto' }}
      >
        <defs>
          {/* Glow filter for dots */}
          <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="4" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
          {/* Soft glow for nodes */}
          <filter id="node-glow" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="6" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
          {/* Gradient for paths */}
          {(Object.entries(COLORS) as [ArmKey, string][]).map(([arm, color]) => (
            <linearGradient key={arm} id={`grad-${arm}`} gradientUnits="userSpaceOnUse"
              x1={arm === 'grid' ? POS.grid.x  : arm === 'home' ? POS.home.x  : CX}
              y1={arm === 'solar'? POS.solar.y  : arm === 'bat'  ? POS.battery.y: CY}
              x2={CX} y2={CY}>
              <stop offset="0%"   stopColor={color} stopOpacity="0.5" />
              <stop offset="100%" stopColor={color} stopOpacity="0.1" />
            </linearGradient>
          ))}
        </defs>

        {/* ── Path lines ─────────────────────────────────────── */}
        {(Object.entries(PATHS) as [ArmKey, string][]).map(([arm, d]) => (
          <path key={arm} d={d}
            ref={el => { pathRefs.current[arm] = el }}
            stroke={`url(#grad-${arm})`}
            strokeWidth="3.5"
            fill="none"
            strokeLinecap="round"
          />
        ))}

        {/* ── Animated dot elements (pre-allocated) ─────────── */}
        {Array.from({ length: MAX_DOTS * 4 }, (_, i) => (
          <circle key={`dot-${i}`}
            ref={el => { dotEls.current[i] = el }}
            r="6"
            fill={specsRef.current[i]?.color ?? '#fff'}
            filter="url(#glow)"
            style={{ opacity: 0, transition: 'none' }}
          >
            {/* inner bright core */}
          </circle>
        ))}

        {/* ─────── HUB ──────────────────────────────────────── */}
        <circle cx={CX} cy={CY} r={HR + 4}
          fill="#0d1829" stroke="#1e2f4a" strokeWidth="1.5" />
        <circle cx={CX} cy={CY} r={HR - 2}
          fill="#243350" />

        {/* ─────── SOLAR node ───────────────────────────────── */}
        <circle cx={POS.solar.x} cy={POS.solar.y} r={NR + 4}
          fill={pvOn ? '#f0a82008' : 'transparent'}
          stroke={pvOn ? '#f0a820' : '#1e2f4a'}
          strokeWidth="1.5"
          filter={pvOn ? 'url(#node-glow)' : undefined}
        />
        <circle cx={POS.solar.x} cy={POS.solar.y} r={NR}
          fill={pvOn ? '#f0a82014' : '#0d1829'}
          stroke={pvOn ? '#f0a82060' : '#1e2f4a'}
          strokeWidth="1.5"
        />
        <text x={POS.solar.x} y={POS.solar.y - 10}
          textAnchor="middle" dominantBaseline="middle"
          fontSize="22" fill={pvOn ? '#f0a820' : '#2a3f5e'}>☀</text>
        <text x={POS.solar.x} y={POS.solar.y + 13}
          textAnchor="middle" fontSize="11" fontWeight="700"
          fill={pvOn ? '#e4eaf5' : '#3f5572'}>{fmtW(ppv)}</text>
        <text x={POS.solar.x} y={POS.solar.y + NR + 18}
          textAnchor="middle" fontSize="11" fill="#6a82a0">Solar</text>

        {/* ─────── GRID node ────────────────────────────────── */}
        <circle cx={POS.grid.x} cy={POS.grid.y} r={NR}
          fill={gridImp ? '#d44c4414' : gridExp ? '#00a87414' : '#0d1829'}
          stroke={gridImp ? '#d44c4460' : gridExp ? '#00a87460' : '#1e2f4a'}
          strokeWidth="1.5"
        />
        <text x={POS.grid.x} y={POS.grid.y - 10}
          textAnchor="middle" fontSize="22"
          fill={gridImp ? '#e06058' : gridExp ? '#00c49a' : '#2a3f5e'}>⚡</text>
        <text x={POS.grid.x} y={POS.grid.y + 13}
          textAnchor="middle" fontSize="11" fontWeight="700"
          fill={gridImp || gridExp ? '#e4eaf5' : '#3f5572'}>{fmtW(pgrid)}</text>
        <text x={POS.grid.x} y={POS.grid.y - NR - 10}
          textAnchor="middle" fontSize="11" fill="#6a82a0">Grid</text>
        <text x={POS.grid.x} y={POS.grid.y + NR + 18}
          textAnchor="middle" fontSize="10"
          fill={gridImp ? '#e06058' : gridExp ? '#00c49a' : '#2a3f5e'}>
          {gridImp ? '↓ Import' : gridExp ? '↑ Export' : 'Idle'}
        </text>

        {/* ─────── HOME node ────────────────────────────────── */}
        <circle cx={POS.home.x} cy={POS.home.y} r={NR}
          fill="#5590e014" stroke="#5590e060" strokeWidth="1.5" />
        <text x={POS.home.x} y={POS.home.y - 10}
          textAnchor="middle" fontSize="22" fill="#7aaeed">⌂</text>
        <text x={POS.home.x} y={POS.home.y + 13}
          textAnchor="middle" fontSize="11" fontWeight="700"
          fill="#e4eaf5">{fmtW(pload)}</text>
        <text x={POS.home.x} y={POS.home.y - NR - 10}
          textAnchor="middle" fontSize="11" fill="#6a82a0">Home</text>

        {/* ─────── BATTERY node ─────────────────────────────── */}
        <circle cx={POS.battery.x} cy={POS.battery.y} r={NR}
          fill={batChg ? '#00c49a14' : batDis ? '#e0881c14' : '#0d1829'}
          stroke={batChg ? '#00c49a60' : batDis ? '#e0881c60' : '#1e2f4a'}
          strokeWidth="1.5"
        />
        {/* SoC arc */}
        {soc > 1 && (
          <path d={socArc} fill="none" stroke={socColor}
            strokeWidth="3.5" strokeLinecap="round" />
        )}
        <text x={POS.battery.x} y={POS.battery.y + 7}
          textAnchor="middle" fontSize="13" fontWeight="800"
          fill={socColor}>{soc}%</text>
        <text x={POS.battery.x} y={POS.battery.y + NR + 18}
          textAnchor="middle" fontSize="11" fill="#6a82a0">Battery</text>
        {(batChg || batDis) && (
          <text x={POS.battery.x} y={POS.battery.y - NR - 10}
            textAnchor="middle" fontSize="10"
            fill={batChg ? '#00c49a' : '#e0881c'}>
            {batChg ? `↑ ${fmtW(pbattery)}` : `↓ ${fmtW(pbattery)}`}
          </text>
        )}

        {/* ─────── Re-paint dots with correct colour ────────── */}
        {/* Dots are pre-rendered above; we refresh their fill each render */}
        {specsRef.current.map((spec, i) => {
          const el = dotEls.current[i]
          if (el) el.setAttribute('fill', spec.color)
          return null
        })}
      </svg>
    </div>
  )
}
