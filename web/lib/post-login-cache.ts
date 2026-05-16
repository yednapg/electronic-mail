import { warmAppSession } from './app-session-store';

export async function warmPostLoginCaches(): Promise<void> {
  await warmAppSession();
}
