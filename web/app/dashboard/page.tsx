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
  return <DashboardClient />;
}
