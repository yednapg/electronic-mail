'use client';

import type { ReactNode } from 'react';

import type { FeedItem } from '../lib/types';

type FeedSectionProps = {
  title: string;
  items: FeedItem[];
  children: ReactNode;
};

export function FeedSection({ title, items, children }: FeedSectionProps) {
  return (
    <section className="planner-section">
      <div className="planner-section-label">{title}</div>
      {items.length === 0 ? <div className="planner-empty-card">Nothing to show right now.</div> : children}
    </section>
  );
}
