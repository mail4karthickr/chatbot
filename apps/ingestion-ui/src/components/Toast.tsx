import { useAppSelector } from '../app/hooks'

export function Toast() {
  const flash = useAppSelector((s) => s.ui.flash)
  if (!flash) return null
  return <div className="toast">{flash}</div>
}
