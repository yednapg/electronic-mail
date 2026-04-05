/** Redirect the site root to the dashboard entrypoint. */
import { redirect } from 'next/navigation';

export default function HomePage() {
  redirect('/dashboard');
}
