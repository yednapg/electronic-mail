'use client';

import { useState } from 'react';

import type { DashboardSectionItem } from './types';

type DashboardSectionProps = {
  title: string;
  items: DashboardSectionItem[];
};

export function DashboardSection({ title, items }: DashboardSectionProps) {
  return (
    <section className="digest-section" aria-label={title}>
      <div className="digest-section-header">
        <h2 className="digest-section-title">{title}</h2>
      </div>

      <ul className="attention-list">
        {items.map((item) => (
          <DashboardSectionItemRow key={item.id} item={item} />
        ))}
      </ul>
    </section>
  );
}

function DashboardSectionItemRow({ item }: { item: DashboardSectionItem }) {
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
