export type DashboardSummaryData = {
  greeting: string;
  name: string;
  meetingCount: number;
  taskCount: number;
  replyCount: number;
  paymentCount: number;
  freeAfterLabel: string;
};

export type DashboardAgendaItem = {
  id: string;
  time: string;
  title: string;
  allDay?: boolean;
  tone: 'blue' | 'cyan' | 'green' | 'teal' | 'lime';
};

export type DashboardActionTone = 'blue' | 'green';

export type DashboardSectionItem = {
  id: string;
  title: string;
  checked?: boolean;
  cta?: {
    label: string;
    tone: DashboardActionTone;
  };
};

export type DashboardSectionData = {
  id: string;
  title: 'Now' | 'Today' | 'Later';
  items: DashboardSectionItem[];
};
