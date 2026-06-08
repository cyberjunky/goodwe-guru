/**
 * Demo / screenshot mode — provides realistic fake data without any backend.
 * Activated by adding ?demo=1 to the URL.
 */
import { type ReactNode, useState, useEffect } from 'react'
import { InverterContext } from './InverterContext'
import type { InverterData, ConnectionStatus } from './InverterContext'
import { MOCK_DATA, buildLiveHistory, MOCK_SETTINGS } from '../mockData'

export function DemoProvider({ children }: { children: ReactNode }) {
  const [lastUpdate, setLastUpdate] = useState(new Date())

  useEffect(() => {
    const id = setInterval(() => setLastUpdate(new Date()), 10_000)
    return () => clearInterval(id)
  }, [])

  return (
    <InverterContext.Provider value={{
      data:         MOCK_DATA as unknown as InverterData,
      history:      buildLiveHistory(),
      status:       'connected' as ConnectionStatus,
      lastUpdate,
      settings:     { ...MOCK_SETTINGS, platform: 'ET' },
      platform:     'ET',
      loadSettings: () => {},
      writeSetting: async () => {},
    }}>
      {children}
    </InverterContext.Provider>
  )
}
