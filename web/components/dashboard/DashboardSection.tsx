'use client';

/** Render one checklist-style dashboard section and manage local checked state. */
import { useState, type CSSProperties } from 'react';

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
        {visibleItems.map((item, index) => (
          <DashboardSectionItemRow key={item.id} item={item} index={index} />
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

function DashboardSectionItemRow({
  item,
  index,
}: {
  item: DashboardSectionItem;
  index: number;
}) {
  const [ctaState, setCtaState] = useState<'idle' | 'loading' | 'done'>('idle');
  const detailId = `attention-detail-${item.id}`;
  const checkboxId = `attention-check-${item.id}`;
  const copyClassName = [
    'attention-copy',
    item.cta?.placement === 'prefix' ? 'has-prefix-cta' : '',
    item.cta && item.cta.placement !== 'prefix' ? 'has-suffix-cta' : '',
  ]
    .filter(Boolean)
    .join(' ');

  async function runCtaAction() {
    if (item.cta?.action === undefined || ctaState !== 'idle') {
      return;
    }

    if (process.env.NEXT_PUBLIC_DEMO_MODE !== 'false') {
      setCtaState('done');
      return;
    }

    setCtaState('loading');

    try {
      const { threadId, operation } = item.cta.action;
      const response = await fetch(`http://localhost:3001/gmail/threads/${threadId}/${operation}`, {
        method: 'POST',
      });

      if (!response.ok) {
        throw new Error(`Failed to ${operation} Gmail thread`);
      }

      setCtaState('done');
    } catch (_error) {
      setCtaState('idle');
    }
  }

  const itemStyle = { '--attention-index': index } as CSSProperties;

  return (
    <li className={`attention-item ${item.detail ? 'attention-item-expandable' : ''}`} style={itemStyle}>
      <input id={checkboxId} type="checkbox" className="attention-checkbox-input" defaultChecked={item.checked ?? false} />
      <div className="attention-item-line">
        <label className="attention-checkbox" htmlFor={checkboxId} aria-label={`Toggle ${item.title}`}>
          <span className="attention-checkbox-mark" aria-hidden="true" />
        </label>
        {item.detail ? (
          <details className="attention-details-native">
            <summary className={`${copyClassName} attention-copy-button`} aria-controls={detailId}>
              <span>{item.title}</span>
            </summary>
            <DashboardItemDetail id={detailId} item={item} checkboxId={checkboxId} />
          </details>
        ) : (
          <p className={copyClassName}>
            {item.cta?.placement === 'prefix' ? (
              <>
                <SectionCta item={item} ctaState={ctaState} runCtaAction={runCtaAction} />
                <span> </span>
              </>
            ) : null}
            <span>{item.title}</span>
            {item.cta && item.cta.placement !== 'prefix' ? (
              <span className="attention-action">
                <span className="attention-arrow" aria-hidden="true">
                  {' '}
                  →{' '}
                </span>
                <SectionCta item={item} ctaState={ctaState} runCtaAction={runCtaAction} />
              </span>
            ) : null}
          </p>
        )}
      </div>
    </li>
  );
}

function DashboardItemDetail({
  id,
  item,
  checkboxId,
}: {
  id: string;
  item: DashboardSectionItem;
  checkboxId: string;
}) {
  if (!item.detail) {
    return null;
  }

  return (
    <div id={id} className="attention-detail">
      <div className="attention-detail-clip">
        <div className="attention-detail-panel">
          <div className="attention-detail-body">
            {item.detail.facts !== undefined && item.detail.facts.length > 0 ? (
              <dl className="attention-detail-facts">
                {item.detail.facts.map((fact) => (
                  <div key={`${fact.label}-${fact.value}`} className="attention-detail-fact">
                    <dt>{fact.label}</dt>
                    <dd>{fact.value}</dd>
                  </div>
                ))}
              </dl>
            ) : null}
            {item.detail.body.map((line) => (
              <p key={line}>{line}</p>
            ))}
            {item.detail.evidence !== undefined && item.detail.evidence.length > 0 ? (
              <div className="attention-detail-evidence">
                <p className="attention-detail-evidence-title">Source evidence</p>
                <ul>
                  {item.detail.evidence.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
          <div className="attention-detail-actions" aria-label={`Actions for ${item.title}`}>
            <label htmlFor={checkboxId} className="attention-detail-action attention-detail-action-primary">
              {item.detail.confirmLabel}
            </label>
            <span className="attention-detail-divider" aria-hidden="true">
              |
            </span>
            <span className="attention-detail-action attention-detail-action-secondary">
              {item.detail.dismissLabel}
            </span>
          </div>
          {item.detail.links ? (
            <div className="attention-detail-links" aria-label={`Evidence links for ${item.title}`}>
              <a href={item.detail.links.rawHref}>Raw emails</a>
              <span aria-hidden="true">|</span>
              <a href={item.detail.links.traceHref}>Pipeline history</a>
            </div>
          ) : null}
          <p className="attention-detail-source">{item.detail.sourceLabel}</p>
        </div>
      </div>
    </div>
  );
}

function SectionCta({
  item,
  ctaState,
  runCtaAction,
}: {
  item: DashboardSectionItem;
  ctaState: 'idle' | 'loading' | 'done';
  runCtaAction: () => void;
}) {
  if (!item.cta) {
    return null;
  }

  const label = ctaState === 'done' ? 'Done' : ctaState === 'loading' ? 'Working...' : item.cta.label;

  return item.cta.action ? (
    <button
      type="button"
      className={`attention-cta attention-cta-${item.cta.tone} attention-cta-button`}
      onClick={runCtaAction}
      disabled={ctaState !== 'idle'}
    >
      {label}
    </button>
  ) : (
    <span className={`attention-cta attention-cta-${item.cta.tone}`}>{label}</span>
  );
}
