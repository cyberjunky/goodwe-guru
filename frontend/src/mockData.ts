/** Realistic fake inverter data for demo/screenshot mode */
export const MOCK_DATA = {
  // PV strings
  ppv: 4230, ppv1: 1260, ppv2: 1180, ppv3: 980, ppv4: 810,
  vpv1: 362.4, vpv2: 358.1, vpv3: 341.7, vpv4: 328.9,
  ipv1: 3.48, ipv2: 3.30, ipv3: 2.87, ipv4: 2.46,
  // Grid – exporting
  pgrid: -1390, pgrid2: -470, pgrid3: -455,
  vgrid: 231.4, vgrid2: 230.8, vgrid3: 232.1,
  igrid: -2.01, igrid2: -2.04, igrid3: -1.96,
  fgrid: 50.02, fgrid2: 50.02, fgrid3: 50.02,
  grid_in_out_label: 'Exporting',
  // Battery – charging
  pbattery1: 1840, vbattery1: 51.6, ibattery1: 35.7,
  battery_soc: 78, battery_soh: 97,
  battery_mode_label: 'Charging',
  battery_temperature: 28.4,
  battery_charge_limit: 100, battery_discharge_limit: 100,
  battery_status: 0,
  battery_max_cell_voltage: 3.412, battery_min_cell_voltage: 3.398,
  battery_max_cell_temp: 29.1,   battery_min_cell_temp: 27.8,
  // Load / backup
  load_ptotal: 1000, backup_ptotal: 0,
  // Temperatures
  temperature: 42.3, temperature_air: 36.1, temperature_module: 44.8,
  // Energy counters – today
  e_day: 18.72, e_day_exp: 9.14, e_day_imp: 0.88,
  e_load_day: 8.46,
  e_bat_charge_day: 6.30, e_bat_discharge_day: 1.10,
  // Energy counters – all-time
  e_total: 8_432_100, e_total_exp: 3_912_400, e_total_imp: 620_700,
  e_load_total: 4_840_500,
  e_bat_charge_total: 2_102_600, e_bat_discharge_total: 1_987_300,
  h_total: 14_820,
  // Status
  work_mode_label: 'Self-Use Mode',
  safety_country_label: 'Netherlands',
  error_codes: '0',
  diagnose_result_label: 'Normal',
  // Meter
  meter_active_power1: -462, meter_active_power2: -455, meter_active_power3: -473,
  // BMS (GoodWe)
  bms_bat_soc: 78, bms_bat_voltage: 51.6, bms_bat_current: 35.7,
  bms_bat_temperature: 28.1, bms_status: 0,
}

export const MOCK_SETTINGS = {
  work_mode: 5, ems_mode: 1,
  grid_export: true, grid_export_limit: 6000,
  battery_capacity: 200, battery_discharge_depth: 10,
  battery_discharge_depth_offline: 10, battery_soc_protection: 5,
  backup_supply: true, fast_charging: false,
  shadow_scan: true, pen_relay: false, dred: false,
  peak_shaving_power_limit: 3000, peak_shaving_soc: 20,
}

/** 2-hour live power history (one point per 10s) */
export function buildLiveHistory() {
  const out = []
  const now = Date.now()
  for (let i = 720; i >= 0; i--) {
    const t = now - i * 10_000
    const sun = Math.max(0, Math.sin((new Date(t).getHours() - 6) * Math.PI / 14))
    const pv  = +(4800 * sun * (0.90 + 0.10 * Math.random())).toFixed(0)
    const bat = +(1800 * (0.9 + 0.2 * Math.random())).toFixed(0)
    const load = +(1000 + 300 * Math.random()).toFixed(0)
    const grid = -(Math.max(0, +pv - +bat - +load))
    out.push({ ts: t, ppv: +pv, pgrid: +grid, pbattery: +bat, load: +load })
  }
  return out
}
