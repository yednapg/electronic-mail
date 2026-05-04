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
  const [expandedItemId, setExpandedItemId] = useState<string | null>(null);
  const visibleItems = hasOverflow && !expanded ? items.slice(0, maxVisible) : items;
  const overflowCount = hasOverflow ? items.length - (maxVisible ?? items.length) : 0;

  return (
    <section className="digest-section" aria-label={title}>
      <div className="digest-section-header">
        <h2 className="digest-section-title">{title}</h2>
      </div>

      <ul className="attention-list">
        {visibleItems.map((item) => (
          <DashboardSectionItemRow
            key={item.id}
            item={item}
            expanded={expandedItemId === item.id}
            onToggleDetail={() => {
              setExpandedItemId((currentId) => (currentId === item.id ? null : item.id));
            }}
          />
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
  expanded,
  onToggleDetail,
}: {
  item: DashboardSectionItem;
  expanded: boolean;
  onToggleDetail: () => void;
}) {
  // Checkboxes are intentionally local-only; they are a UI affordance, not persisted state.
  const [checked, setChecked] = useState(item.checked ?? false);
  const [ctaState, setCtaState] = useState<'idle' | 'loading' | 'done'>('idle');
  const detailId = `attention-detail-${item.id}`;
  const copyClassName = [
    'attention-copy',
    checked ? 'is-checked' : '',
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

  return (
    <li className={`attention-item ${item.detail ? 'attention-item-expandable' : ''}`}>
      <div className="attention-item-line">
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
        {item.detail ? (
          <button
            type="button"
            className={`${copyClassName} attention-copy-button`}
            aria-expanded={expanded}
            aria-controls={detailId}
            onClick={onToggleDetail}
          >
            <span>{item.title}</span>
          </button>
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

      {item.detail ? (
        <DashboardItemDetail
          id={detailId}
          item={item}
          expanded={expanded}
          onConfirm={() => setChecked(true)}
          onDismiss={() => setChecked(false)}
        />
      ) : null}
    </li>
  );
}

function DashboardItemDetail({
  id,
  item,
  expanded,
  onConfirm,
  onDismiss,
}: {
  id: string;
  item: DashboardSectionItem;
  expanded: boolean;
  onConfirm: () => void;
  onDismiss: () => void;
}) {
  const [decision, setDecision] = useState<'yes' | 'no' | null>(null);

  if (!item.detail) {
    return null;
  }

  return (
    <div id={id} className={`attention-detail ${expanded ? 'is-expanded' : ''}`} aria-hidden={!expanded}>
      <div className="attention-detail-clip">
        <div className="attention-detail-panel">
          <div className="attention-detail-body">
            {item.detail.body.map((line) => (
              <p key={line}>{line}</p>
            ))}
          </div>
          <div className="attention-detail-actions" aria-label={`Actions for ${item.title}`}>
            <button
              type="button"
              className={`attention-detail-action attention-detail-action-primary ${
                decision === 'yes' ? 'is-selected' : ''
              }`}
              disabled={!expanded}
              onClick={() => {
                setDecision('yes');
                onConfirm();
              }}
            >
              {item.detail.confirmLabel}
            </button>
            <span className="attention-detail-divider" aria-hidden="true">
              |
            </span>
            <button
              type="button"
              className={`attention-detail-action attention-detail-action-secondary ${
                decision === 'no' ? 'is-selected' : ''
              }`}
              disabled={!expanded}
              onClick={() => {
                setDecision('no');
                onDismiss();
              }}
            >
              {item.detail.dismissLabel}
            </button>
          </div>
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
