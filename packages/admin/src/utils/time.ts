export function formatTime(ts: string | null | undefined): string {
  if (!ts) return '-'
  try { return new Date(ts).toLocaleString('zh-CN', { hour12: false }) }
  catch { return ts }
}
