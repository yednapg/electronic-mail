import { notFound } from 'next/navigation';

import { isWebProductUIEnabled } from '../../lib/web-product-boundary';
import { DashboardClient } from './DashboardClient';

export {
  buildAgenda,
  buildSections,
  buildSummary,
  shouldKeepNaturalTitle,
  startsWithActionVerb,
  startsWithVerb,
  stripDuePrefix,
  toActionSentence,
  toSectionCta,
  toSectionDetail,
  toSectionItem,
  toneForTimingBand,
} from '../../lib/dashboard-view-model';

export default function DashboardPage() {
  if (!isWebProductUIEnabled()) {
    notFound();
  }

  return <DashboardClient />;
}
