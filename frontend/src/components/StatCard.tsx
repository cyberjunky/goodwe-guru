interface StatCardProps {
  label: string
  value: string | number
  unit?: string
  sub?: string
  color?: string
  icon?: React.ReactNode
  accent?: boolean
}

export default function StatCard({ label, value, unit, sub, color = 'text-white', icon, accent }: StatCardProps) {
  return (
    <div className={`rounded-xl p-4 flex flex-col gap-1.5 border transition-colors ${
      accent
        ? 'bg-gradient-to-br from-gray-800/80 to-gray-900 border-gray-700 hover:border-gray-600'
        : 'bg-gradient-to-br from-gray-800/40 to-gray-900 border-gray-800 hover:border-gray-700'
    }`}>
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium text-gray-500 uppercase tracking-wider leading-none">{label}</span>
        {icon && <span className={`${color} opacity-60`}>{icon}</span>}
      </div>
      <div className={`text-2xl font-bold tracking-tight ${color} flex items-baseline gap-1.5 leading-none mt-0.5`}>
        {value}
        {unit && <span className="text-sm font-normal text-gray-500">{unit}</span>}
      </div>
      {sub && <div className="text-[11px] text-gray-500 leading-tight">{sub}</div>}
    </div>
  )
}
