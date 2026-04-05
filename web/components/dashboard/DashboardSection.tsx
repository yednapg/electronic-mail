'use client';

/** Render one checklist-style dashboard section and manage local checked state. */
import { useState } from 'react';

import type { DashboardSectionItem } from './types';

type DashboardSectionProps = {
  title: string;
  items: DashboardSectionItem[];
  maxVisible?: number;
  collapsedByDefault?: boolean;
};

export function DashboardSection({
  title,
  items,
  maxVisible,
  collapsedByDefault = true,
}: DashboardSectionProps) {
  const hasOverflow = maxVisible !== undefined && items.length > maxVisible;
  const [expanded, setExpanded] = useState(!hasOverflow || !collapsedByDefault);
  const visibleItems = hasOverflow && !expanded ? items.slice(0, maxVisible) : items;
  const overflowCount = hasOverflow ? items.length - (maxVisible ?? items.length) : 0;

  return (
    <section className="digest-section" aria-label={title}>
      <div className="digest-section-header">
        <h2 className="digest-section-title">{title}</h2>
      </div>

      <ul className="attention-list">
        {visibleItems.map((item) => (
          <DashboardSectionItemRow key={item.id} item={item} />
        ))}
      </ul>

      {hasOverflow ? (
        <button
          type="button"
          className="digest-section-toggle"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
        >
          {expanded ? 'Show less' : `Show ${overflowCount} more`}
        </button>
      ) : null}
    </section>
  );
}

function DashboardSectionItemRow({ item }: { item: DashboardSectionItem }) {
  // Checkboxes are intentionally local-only; they are a UI affordance, not persisted state.
  const [checked, setChecked] = useState(item.checked ?? false);

  return (
    <li className="attention-item">
      <button
        type="button"
        className={`attention-checkbox ${checked ? 'is-checked' : ''}`}
        aria-pressed={checked}
        aria-label={`${checked ? 'Untick' : 'Tick'} ${item.title}`}
        onClick={() => setChecked((value) => !value)}
      >
        <span className="attention-checkbox-mark" aria-hidden="true">
          {checked ? '✓' : ''}
        </span>
      </button>
      <p className={`attention-copy ${checked ? 'is-checked' : ''}`}>
        <span>{item.title}</span>
        {item.cta ? (
          <>
            <span className="attention-arrow" aria-hidden="true">
              {' '}
              →{' '}
            </span>
            <span className={`attention-cta attention-cta-${item.cta.tone}`}>{item.cta.label}</span>
          </>
        ) : null}
      </p>
    </li>
  );
}
