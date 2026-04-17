/** Small view-model types consumed by the dashboard components. */
export type DashboardSummaryData = {
  headline: string;
  brief: string;
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
  title: string;
  checked?: boolean;
  cta?: {
    label: string;
    tone: DashboardActionTone;
    action?: {
      kind: 'gmail-thread';
      threadId: string;
      operation: 'archive' | 'unarchive';
    };
  };
};

export type DashboardSectionData = {
  id: string;
  title: 'Now' | 'Today' | 'Worth Knowing';
  items: DashboardSectionItem[];
  maxVisible?: number;
  collapsedByDefault?: boolean;
};
