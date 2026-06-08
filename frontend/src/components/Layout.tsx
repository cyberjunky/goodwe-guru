import { NavLink, Outlet } from 'react-router-dom'
import {
  LayoutDashboard, Battery, Sun, Zap, Settings, BarChart2,
  AlertTriangle, Wifi, WifiOff, RefreshCw, Activity,
  Euro, CloudSun, Bolt,
} from 'lucide-react'
import { useInverter } from '../context/InverterContext'

/** Friendly smiling-sun brand mark (dark face on the amber chip). */
function BrandSun({ size = 18 }: { size?: number }) {
  const c = '#3a2400'
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <g stroke={c} strokeWidth="1.7" strokeLinecap="round">
        {[0, 45, 90, 135, 180, 225, 270, 315].map(a => {
          const r = (a * Math.PI) / 180
          return (
            <line key={a}
              x1={12 + 8.2 * Math.cos(r)} y1={12 + 8.2 * Math.sin(r)}
              x2={12 + 10.6 * Math.cos(r)} y2={12 + 10.6 * Math.sin(r)} />
          )
        })}
      </g>
      <circle cx="12" cy="12" r="6.6" stroke={c} strokeWidth="1.7" fill="none" />
      <circle cx="9.7" cy="11" r="0.95" fill={c} />
      <circle cx="14.3" cy="11" r="0.95" fill={c} />
      <path d="M9.3 13.4 Q12 15.9 14.7 13.4" stroke={c} strokeWidth="1.6" strokeLinecap="round" fill="none" />
    </svg>
  )
}

const NAV = [
  { to: '/',          icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/solar',     icon: Sun,             label: 'Solar'     },
  { to: '/battery',   icon: Battery,         label: 'Battery'   },
  { to: '/grid',      icon: Zap,             label: 'Grid'      },
  { to: '/finance',   icon: Euro,            label: 'Finance'   },
  { to: '/forecast',     icon: CloudSun,     label: 'Forecast'    },
  { to: '/automations', icon: Bolt,         label: 'Automations' },
  { to: '/history',     icon: BarChart2,    label: 'History'     },
  { to: '/settings',  icon: Settings,        label: 'Settings'  },
  { to: '/faults',    icon: AlertTriangle,   label: 'Faults'    },
]

function ConnectionChip() {
  const { status, lastUpdate, data } = useInverter()
  return (
    <div className="flex items-center gap-2 px-3 py-2.5">
      {status === 'connected'
        ? <Wifi size={12} className="text-emerald-400 shrink-0" />
        : status === 'connecting'
        ? <RefreshCw size={12} className="text-amber-400 shrink-0 animate-spin" />
        : <WifiOff size={12} className="text-red-400 shrink-0" />}
      <span className="text-[11px] text-gray-500 truncate hidden lg:block">
        {status === 'connected' && lastUpdate
          ? `Live · ${lastUpdate.toLocaleTimeString()}`
          : status === 'connecting' ? 'Connecting…' : 'Disconnected'}
      </span>
      {data && (
        <span className="hidden xl:flex items-center gap-1 ml-1 text-amber-400 text-[11px] font-semibold">
          <Activity size={10} />
          {((data.ppv as number ?? 0) / 1000).toFixed(2)} kW
        </span>
      )}
    </div>
  )
}

export default function Layout() {
  return (
    <div className="flex h-[100dvh] overflow-hidden" style={{ background: '#070c18' }}>

      {/* ── Sidebar ──────────────────────────────────────────────── */}
      <aside className="hidden md:flex flex-col w-14 lg:w-52 shrink-0 border-r"
        style={{ background: '#0a0f1e', borderColor: '#141f35' }}>

        {/* Logo */}
        <div className="h-16 flex items-center justify-center lg:justify-start px-3 lg:px-4 gap-3 border-b" style={{ borderColor: '#141f35' }}>
          <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: 'linear-gradient(135deg, #e8950a 0%, #f7b733 100%)', boxShadow: '0 2px 10px rgba(232,149,10,0.35)' }}>
            <BrandSun size={20} />
          </div>
          <div className="hidden lg:block leading-tight">
            <div className="text-[15px] font-semibold text-white tracking-tight">GoodWe Guru</div>
            <div className="text-[10px] text-amber-500/80 font-medium tracking-wide">Solar Dashboard</div>
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto py-3 px-2 space-y-0.5">
          {NAV.map(({ to, icon: Icon, label }) => (
            <NavLink key={to} to={to} end={to === '/'}
              className={({ isActive }) =>
                'flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[13px] transition-all duration-150 ' +
                (isActive
                  ? 'text-amber-400 font-medium'
                  : 'text-gray-500 hover:text-gray-200')
              }
              style={({ isActive }) => isActive ? { background: 'rgba(232,149,10,0.10)' } : {}}
            >
              <Icon size={16} className="shrink-0" />
              <span className="hidden lg:block">{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="border-t" style={{ borderColor: '#141f35' }}>
          <ConnectionChip />
        </div>
      </aside>

      {/* ── Main ─────────────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Mobile top bar */}
        <header className="md:hidden flex items-center justify-between h-12 px-4 border-b shrink-0"
          style={{ background: '#0a0f1e', borderColor: '#141f35' }}>
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #e8950a, #f7b733)' }}>
              <BrandSun size={17} />
            </div>
            <span className="font-semibold text-[14px] text-white tracking-tight">GoodWe Guru</span>
          </div>
          <ConnectionChip />
        </header>

        <main className="flex-1 overflow-y-auto pb-20 md:pb-0">
          <Outlet />
        </main>
      </div>

      {/* ── Mobile bottom nav ─────────────────────────────────────── */}
      <nav className="md:hidden fixed bottom-0 inset-x-0 z-50 flex items-stretch h-16 border-t"
        style={{ background: '#0a0f1e', borderColor: '#141f35' }}>
        {NAV.map(({ to, icon: Icon, label }) => (
          <NavLink key={to} to={to} end={to === '/'}
            className={({ isActive }) =>
              'flex-1 flex flex-col items-center justify-center gap-0.5 text-[9px] font-medium transition-colors ' +
              (isActive ? 'text-amber-400' : 'text-gray-600')
            }
          >
            <Icon size={19} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
