import { env } from '../env';

type DecisionServiceEntityInput = {
  id: string;
  source: 'gmail' | 'calendar';
  subject: string;
  body: string;
  sender: string;
  participants: string[];
  timestamp: string;
  due_at: string | null;
  thread_summary: string | null;
};

type DecisionServiceItem = {
  id: string;
  is_decision: boolean;
  title: string;
  why_this_is_here: string;
  primary_action: string;
  timing_band: 'now' | 'today' | 'later' | 'hidden';
  importance_level: 'high' | 'medium' | 'low';
  action_confidence: 'high' | 'medium' | 'low';
};

type DecisionServiceResponse = {
  items: DecisionServiceItem[];
};

type CalendarContextItemInput = {
  id: string;
  subject: string;
  participants: string[];
  timing_band: 'now' | 'today' | 'later' | 'hidden';
  day_phrase: string;
  time_phrase: string | null;
};

type CalendarContextItem = {
  id: string;
  why_this_is_here: string;
};

type CalendarContextResponse = {
  items: CalendarContextItem[];
};

export async function decideEntities(
  entities: DecisionServiceEntityInput[],
): Promise<DecisionServiceItem[]> {
  if (entities.length === 0) {
    return [];
  }

  const response = await fetch(env.aiServiceUrl, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
    },
    body: JSON.stringify({ entities }),
  });

  if (!response.ok) {
    throw new Error(`Decision service request failed with status ${response.status}`);
  }

  const payload = (await response.json()) as DecisionServiceResponse;

  return payload.items;
}

export async function describeCalendarContext(
  items: CalendarContextItemInput[],
): Promise<CalendarContextItem[]> {
  if (items.length === 0) {
    return [];
  }

  const response = await fetch(getCalendarContextUrl(), {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
    },
    body: JSON.stringify({ items }),
  });

  if (!response.ok) {
    throw new Error(`Calendar context request failed with status ${response.status}`);
  }

  const payload = (await response.json()) as CalendarContextResponse;

  return payload.items;
}

function getCalendarContextUrl(): string {
  return env.aiServiceUrl.replace(/\/decide$/, '/calendar-context');
}

export type {
  CalendarContextItem,
  CalendarContextItemInput,
  DecisionServiceEntityInput,
  DecisionServiceItem,
};
