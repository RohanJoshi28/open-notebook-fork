const DRIVE_REGEX = /(?:drive\.google\.com|docs\.google\.com)/i

export function isDriveUrl(url?: string | null): boolean {
  if (!url) return false
  try {
    const u = new URL(url)
    return DRIVE_REGEX.test(u.hostname + u.pathname)
  } catch {
    return false
  }
}
