/** Small view-model types consumed by the dashboard components. */
export type DashboardSummaryData = {
  headline: string;
  brief: string;
  parts?: DashboardSummaryPart[];
  important?: DashboardSummaryImportant | null;
  calendarAvailability?: DashboardCalendarAvailability | null;
};

export type DashboardSummaryPart = {
  type: string;
  emoji: string;
  count: number;
  text: string;
};

export type DashboardSummaryImportant = {
  emoji: string;
  count: number;
  text: string;
  mailGroupId?: string | null;
  actionType?: string | null;
};

export type DashboardCalendarAvailability = {
  emoji: string;
  kind: string;
  time?: string | null;
  text: string;
};

export type DashboardAgendaItem = {
  id: string;
  time: string;
  title: string;
  allDay?: boolean;
  tone: 'blue' | 'green' | 'teal' | 'lime';
};

export type DashboardActionTone = 'blue' | 'green';

export type DashboardSectionItem = {
  id: string;
  entityId?: string;
  title: string;
  checked?: boolean;
  primaryAction?: string;
  needType?: string;
  source?: string;
  detail?: {
    facts?: Array<{
      label: string;
      value: string;
    }>;
    body: string[];
    evidence?: string[];
    actionLabel?: string;
    actionUrl?: string | null;
    confirmLabel: string;
    dismissLabel: string;
    sourceLabel: string;
    links?: {
      threadHref?: string;
    };
  };
  cta?: {
    label: string;
    tone: DashboardActionTone;
    placement?: 'prefix' | 'suffix';
    action?: {
      kind: 'gmail-thread';
      threadId: string;
      operation: 'archive' | 'unarchive';
    };
  };
};

export type DashboardSectionData = {
  id: 'now' | 'today' | 'worth-knowing';
  title: 'Now' | 'Today' | 'Worth Knowing';
  items: DashboardSectionItem[];
  maxVisible?: number;
  collapsedByDefault?: boolean;
};
