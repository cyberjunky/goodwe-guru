import { useState } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { InverterProvider } from './context/InverterContext'
import { DemoProvider } from './context/DemoProvider'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Solar from './pages/Solar'
import Battery from './pages/Battery'
import Grid from './pages/Grid'
import History from './pages/History'
import Settings from './pages/Settings'
import Faults from './pages/Faults'
import Login from './pages/Login'
import Finance from './pages/Finance'
import Forecast from './pages/Forecast'
import Automations from './pages/Automations'

const DEMO = new URLSearchParams(window.location.search).has('demo')

function AppRoutes() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="solar"    element={<Solar />} />
          <Route path="battery"  element={<Battery />} />
          <Route path="grid"     element={<Grid />} />
          <Route path="history"  element={<History />} />
          <Route path="finance"  element={<Finance />} />
          <Route path="forecast"     element={<Forecast />} />
          <Route path="automations" element={<Automations />} />
          <Route path="settings"    element={<Settings />} />
          <Route path="faults"   element={<Faults />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default function App() {
  const [token, setToken] = useState(() => localStorage.getItem('gw_token') ?? '')

  if (DEMO) {
    return <DemoProvider><AppRoutes /></DemoProvider>
  }

  if (!token) return <Login onLogin={setToken} />

  return (
    <InverterProvider token={token} onAuthFail={() => { localStorage.removeItem('gw_token'); setToken('') }}>
      <AppRoutes />
    </InverterProvider>
  )
}
