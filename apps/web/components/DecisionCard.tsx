'use client';

import { ArrowRight } from 'lucide-react';
import { useState } from 'react';

import type { FeedItem } from '../lib/types';
import { formatDisplayTitle, formatDueDate, getItemEmoji } from '../lib/formatting';

type DecisionCardProps = {
  item: FeedItem;
};

export function DecisionCard({ item }: DecisionCardProps) {
  const [expanded, setExpanded] = useState(false);
  const formattedTitle = formatDisplayTitle(item.title);
  const formattedCreatedAt = formatDueDate(item.created_at);
  const isUrgent = item.timing_band === 'now';
  const timingLabel = item.timing_band === 'now' ? 'Now' : item.timing_band === 'today' ? 'Today' : '';
  const actionLabel = item.primary_action === 'none' ? 'view details' : item.primary_action;
  const description = item.why_this_is_here;

  return (
    <article className={`decision-card ${isUrgent ? 'is-urgent' : ''}`}>
      <div className="decision-card-inner">
        <div className="decision-card-emoji">{getItemEmoji(item)}</div>

        <div className="decision-card-copy">
          <div className="decision-card-title-row">
            <h3 className="decision-card-title">{formattedTitle}</h3>
            {isUrgent ? <span className="decision-card-urgent">Urgent</span> : null}
          </div>

          <p className="decision-card-description">{description}</p>

          <button className={`decision-button ${isUrgent ? 'is-urgent' : ''}`}>
            {actionLabel}
            <ArrowRight className="planner-inline-icon" />
          </button>

          <div className="decision-context">
            <div className="decision-meta">
            {timingLabel ? (
              <span className={`decision-pill ${item.timing_band === 'now' ? 'is-now' : 'is-today'}`}>
                {timingLabel}
              </span>
            ) : null}
              {item.effort_level === 'deep' ? <span className="decision-pill">Takes time</span> : null}
              {formattedCreatedAt ? <span className="decision-date">{formattedCreatedAt}</span> : null}
            </div>

            <button className="decision-expand" onClick={() => setExpanded((value) => !value)}>
              {expanded ? 'Hide context' : 'Why this matters'}
            </button>

            {expanded ? <div className="decision-explanation">{item.why_this_is_here}</div> : null}
          </div>
        </div>
      </div>
    </article>
  );
}
