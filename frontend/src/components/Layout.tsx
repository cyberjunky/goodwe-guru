import { NavLink, Outlet } from 'react-router-dom'
import {
  LayoutDashboard, Battery, Sun, Zap, Settings, BarChart2,
  AlertTriangle, Wifi, WifiOff, RefreshCw, Activity,
  Euro, CloudSun, Bolt,
} from 'lucide-react'
import { useInverter } from '../context/InverterContext'

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
        <div className="h-14 flex items-center px-3 lg:px-4 gap-3 border-b" style={{ borderColor: '#141f35' }}>
          <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0"
            style={{ background: 'linear-gradient(135deg, #e8950a 0%, #f0a820 100%)' }}>
            <Sun size={15} className="text-gray-950" />
          </div>
          <div className="hidden lg:block">
            <div className="text-[13px] font-semibold text-white leading-tight">GoodWe Guru</div>
            <div className="text-[10px] text-gray-500 leading-tight">Solar Dashboard</div>
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
            <div className="w-7 h-7 rounded-lg flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #e8950a, #f0a820)' }}>
              <Sun size={14} className="text-gray-950" />
            </div>
            <span className="font-semibold text-[13px] text-white">GoodWe Guru</span>
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
