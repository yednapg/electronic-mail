'use client';

/** Render one mail-group-backed dashboard section and manage local dismissal state. */
import React, { useState, type CSSProperties } from 'react';

import type { DashboardSectionItem } from './types';

type DashboardSectionProps = {
  sectionId: 'now' | 'today' | 'worth-knowing';
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
  const [hiddenItemIds, setHiddenItemIds] = useState<Set<string>>(new Set());
  const sectionItems = items.filter((item) => !hiddenItemIds.has(item.id));
  const hasOverflow = maxVisible !== undefined && sectionItems.length > maxVisible;
  const [expanded, setExpanded] = useState(!hasOverflow || !collapsedByDefault);
  const visibleItems = hasOverflow && !expanded ? sectionItems.slice(0, maxVisible) : sectionItems;
  const overflowCount = hasOverflow ? sectionItems.length - (maxVisible ?? sectionItems.length) : 0;

  return (
    <section className="digest-section" aria-label={title}>
      <div className="digest-section-header">
        <h2 className="digest-section-title">{title}</h2>
        <span className="digest-section-rule" aria-hidden="true" />
      </div>

      {visibleItems.length > 0 ? (
        <ul className="attention-list">
          {visibleItems.map((item, index) => (
            <DashboardSectionItemRow
              key={item.id}
              item={item}
              index={index}
              onComplete={() => setHiddenItemIds((current) => new Set(current).add(item.id))}
            />
          ))}
        </ul>
      ) : (
        <p className="attention-empty">Nothing here yet.</p>
      )}

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
  onComplete,
}: {
  item: DashboardSectionItem;
  index: number;
  onComplete: () => void;
}) {
  const [ctaState, setCtaState] = useState<'idle' | 'loading' | 'done'>('idle');
  const [checked, setChecked] = useState(Boolean(item.checked));
  const [isCompleting, setIsCompleting] = useState(false);
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

    setCtaState('loading');

    try {
      const { threadId, operation } = item.cta.action;
      const response = await fetch(`/api/gmail/threads/${encodeURIComponent(threadId)}/${operation}`, {
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

  function completeItem() {
    if (isCompleting || checked) {
      return;
    }

    setChecked(true);
    setIsCompleting(true);
    onComplete();
  }

  const itemStyle = { '--attention-index': index } as CSSProperties;

  return (
    <li
      className={`attention-item ${item.detail ? 'attention-item-expandable' : ''} ${isCompleting ? 'attention-item-pending' : ''}`}
      style={itemStyle}
    >
      <input
        id={checkboxId}
        type="checkbox"
        className="attention-checkbox-input"
        checked={checked}
        disabled={isCompleting}
        style={{ caretColor: 'transparent' }}
        suppressHydrationWarning
        onChange={(event) => {
          if (event.currentTarget.checked) {
            completeItem();
          } else {
            setChecked(false);
          }
        }}
      />
      <div className="attention-item-line">
        <label className="attention-checkbox" htmlFor={checkboxId} aria-label={`Toggle ${item.title}`}>
          <span className="attention-checkbox-mark" aria-hidden="true" />
        </label>
        {item.detail ? (
          <details className="attention-details-native">
            <summary className={`${copyClassName} attention-copy-button`} aria-controls={detailId}>
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
  const actionCopy = item.detail.actionLabel ?? getDetailFactValue(item.detail.facts, 'Next') ?? item.detail.confirmLabel;

  return (
    <div id={id} className="attention-detail">
      <div className="attention-detail-clip">
        <div className="attention-detail-panel">
          <div className="attention-detail-body">
            {item.detail.body.map((line) => (
              <p key={line}>{line}</p>
            ))}
          </div>
          <div className="attention-detail-next" aria-label={`Action for ${item.title}`}>
            <span className="attention-detail-label">Action</span>
            {item.detail.actionUrl ? (
              <a className="attention-detail-next-copy attention-detail-next-link" href={item.detail.actionUrl}>
                {actionCopy}
              </a>
            ) : (
              <span className="attention-detail-next-copy">{actionCopy}</span>
            )}
          </div>
          <div className="attention-detail-source">
            <span className="attention-detail-label">Sources</span>
            <span className="attention-detail-source-line">
              <span>{formatDetailSourceLabel(item.detail.sourceLabel)}</span>
              {item.detail.links?.threadHref ? (
                <a className="attention-detail-source-link" href={item.detail.links.threadHref}>
                  Read email
                </a>
              ) : null}
            </span>
          </div>
          <div className="attention-detail-actions">
            <label className="attention-detail-complete" htmlFor={checkboxId}>
              {item.detail.confirmLabel}
            </label>
          </div>
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

  const label = ctaState === 'loading' ? 'Working...' : ctaState === 'done' ? 'Done' : item.cta.label;

  return (
    <button
      type="button"
      className={`attention-action-button attention-action-button-${item.cta.tone}`}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        runCtaAction();
      }}
      disabled={ctaState === 'loading' || ctaState === 'done'}
    >
      {label}
    </button>
  );
}

function getDetailFactValue(facts: Array<{ label: string; value: string }> | undefined, label: string) {
  return facts?.find((fact) => fact.label.toLowerCase() === label.toLowerCase())?.value;
}

function formatDetailSourceLabel(label: string) {
  if (label.toLowerCase() === 'gmail') {
    return 'Gmail';
  }
  return label;
}
