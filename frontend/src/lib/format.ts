/** Energy in kWh → friendly { value, unit }: kWh below 1 MWh, MWh above. */
export function fmtEnergy(kwh: number | undefined): { value: string; unit: string } {
  const k = Math.abs(kwh || 0)
  return k >= 1000
    ? { value: (k / 1000).toFixed(2), unit: 'MWh' }
    : { value: k.toFixed(k < 100 ? 1 : 0), unit: 'kWh' }
}
