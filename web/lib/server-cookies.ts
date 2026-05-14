import { cookies } from 'next/headers';

export async function getServerCookieHeader(): Promise<string | null> {
  const cookieStore = await cookies();
  const value = cookieStore.toString();
  return value.length > 0 ? value : null;
}
