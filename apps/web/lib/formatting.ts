import type { FeedItem } from './types';

export function formatTime(date: Date): string {
  return date.toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  });
}

export function formatClockTime(date: Date): string {
  return date.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

export function formatDate(date: Date): string {
  return date.toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  });
}

export function formatDueDate(dateString: string): string {
  const date = toDate(dateString);

  if (date === null) {
    return '';
  }

  const today = new Date();
  const diffDays = Math.ceil((date.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));

  if (diffDays === 0) {
    return 'Today';
  }

  if (diffDays === 1) {
    return 'Tomorrow';
  }

  if (diffDays < 7) {
    return date.toLocaleDateString('en-US', { weekday: 'short' });
  }

  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export function formatScheduleTime(dateString: string): string {
  const date = toDate(dateString);

  if (date === null) {
    return '';
  }

  return formatClockTime(date);
}

export function formatDisplayTitle(title: string): string {
  if (!title.startsWith('Due ')) {
    return title;
  }

  const rawDate = title.slice(4).trim();
  const formatted = formatShortMonthDay(rawDate);

  return formatted ? `Due ${formatted}` : 'Due';
}

export function getTimeOfDay(date: Date): string {
  const hour = date.getHours();

  if (hour < 12) {
    return 'morning';
  }

  if (hour < 18) {
    return 'afternoon';
  }

  return 'evening';
}

export function getItemEmoji(item: FeedItem): string {
  const action = item.primary_action.toLowerCase();
  const title = item.title.toLowerCase();

  if (action.includes('pay') || title.includes('invoice') || title.includes('bill')) {
    return '💳';
  }

  if (action.includes('reply')) {
    return '✉️';
  }

  if (action.includes('confirm')) {
    return '✅';
  }

  if (action.includes('track')) {
    return '📦';
  }

  if (action.includes('open') && title.includes('review')) {
    return '👀';
  }

  return '📋';
}

function formatShortMonthDay(value: string): string {
  const date = toDate(value);

  if (date === null) {
    return '';
  }

  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  });
}

function toDate(value: string): Date | null {
  const normalizedValue =
    value.match(/^\d{4}-\d{2}-\d{2}T[^ ]+/)?.[0] ??
    value.match(/^\d{4}-\d{2}-\d{2}$/)?.[0] ??
    value;
  const date = new Date(normalizedValue);

  if (Number.isNaN(date.getTime())) {
    return null;
  }

  return date;
}
