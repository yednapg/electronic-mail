import { NextResponse } from 'next/server';

import { getDashboard } from '../../../lib/api';
import { buildCommandIndex } from '../../../lib/command-index';

export async function GET() {
  try {
    const dashboard = await getDashboard();
    return NextResponse.json(buildCommandIndex(dashboard));
  } catch (_error) {
    return NextResponse.json(buildCommandIndex(null));
  }
}
