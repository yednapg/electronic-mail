export function isDemoMode(): boolean {
  const value = process.env.NEXT_PUBLIC_UI_DEMO_MODE ?? process.env.UI_DEMO_MODE ?? '';
  return ['1', 'true', 'yes', 'on'].includes(value.trim().toLowerCase());
}
