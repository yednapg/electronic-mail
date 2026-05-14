'use client';

/** Render one checklist-style dashboard section and manage local checked state. */
import React, { useState, type CSSProperties, type FormEvent } from 'react';

import { completeEntity, createGmailDraft, createTask } from '../../lib/api';
import type { DashboardSectionItem } from './types';

type ComposeMode = 'todo' | 'email';

type EmailDraftState = {
  to: string;
  cc: string;
  bcc: string;
  subject: string;
  body: string;
  hasAttachment: boolean;
};

type DashboardSectionProps = {
  sectionId: 'now' | 'today' | 'worth-knowing';
  title: string;
  items: DashboardSectionItem[];
  maxVisible?: number;
  collapsedByDefault?: boolean;
};

const emptyEmailDraft: EmailDraftState = {
  to: '',
  cc: '',
  bcc: '',
  subject: '',
  body: '',
  hasAttachment: false,
};

export function DashboardSection({
  sectionId,
  title,
  items,
  maxVisible,
  collapsedByDefault = true,
}: DashboardSectionProps) {
  const [localItems, setLocalItems] = useState<DashboardSectionItem[]>([]);
  const [composeOpen, setComposeOpen] = useState(false);
  const [composeMode, setComposeMode] = useState<ComposeMode>('todo');
  const [todoTitle, setTodoTitle] = useState('');
  const [todoNotes, setTodoNotes] = useState('');
  const [emailDraft, setEmailDraft] = useState<EmailDraftState>(emptyEmailDraft);
  const [showCc, setShowCc] = useState(false);
  const [showBcc, setShowBcc] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [composeError, setComposeError] = useState<string | null>(null);
  const [hiddenItemIds, setHiddenItemIds] = useState<Set<string>>(new Set());
  const sectionItems = [...localItems, ...items].filter((item) => !hiddenItemIds.has(item.id));
  const hasOverflow = maxVisible !== undefined && sectionItems.length > maxVisible;
  const [expanded, setExpanded] = useState(!hasOverflow || !collapsedByDefault);
  const visibleItems = hasOverflow && !expanded ? sectionItems.slice(0, maxVisible) : sectionItems;
  const overflowCount = hasOverflow ? sectionItems.length - (maxVisible ?? sectionItems.length) : 0;
  const canSubmit =
    composeMode === 'email'
      ? emailDraft.to.trim().length > 0 && formatEmailDraftTitle(emailDraft).length > 0
      : todoTitle.trim().length > 0;

  function updateEmailDraft(key: keyof EmailDraftState, value: string | boolean) {
    setEmailDraft((draft) => ({ ...draft, [key]: value }));
  }

  async function submitInlineComposer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const titleText =
      composeMode === 'email'
        ? formatEmailDraftTitle(emailDraft)
        : todoTitle.trim();
    if (titleText.length === 0) {
      return;
    }

    if (isSubmitting) {
      return;
    }

    setIsSubmitting(true);
    setComposeError(null);

    try {
      if (composeMode === 'email') {
        const draft = await createGmailDraft({
          to: emailDraft.to.trim(),
          cc: emailDraft.cc.trim() || null,
          bcc: emailDraft.bcc.trim() || null,
          subject: emailDraft.subject.trim(),
          body: emailDraft.body.trim(),
        });
        setLocalItems((currentItems) => [
          {
            id: draft.id,
            entityId: draft.entity_id ?? undefined,
            title: titleText,
          },
          ...currentItems,
        ]);
      } else {
        const task = await createTask({
          title: todoTitle.trim(),
          notes: todoNotes.trim() || null,
          section: sectionId === 'worth-knowing' ? 'later' : sectionId,
        });
        setLocalItems((currentItems) => [
          {
            id: task.id,
            entityId: task.entity_id,
            title: task.title,
          },
          ...currentItems,
        ]);
      }

      setTodoTitle('');
      setTodoNotes('');
      setEmailDraft(emptyEmailDraft);
      setShowCc(false);
      setShowBcc(false);
      setComposeMode('todo');
      setComposeOpen(false);
      setExpanded(true);
    } catch (_error) {
      setComposeError(composeMode === 'email' ? 'Could not save this draft.' : 'Could not add this item.');
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <section className="digest-section" aria-label={title}>
      <div className="digest-section-header">
        <h2 className="digest-section-title">{title}</h2>
        <span className="digest-section-rule" aria-hidden="true" />
        <button
          type="button"
          className="digest-section-add"
          aria-label={`Add item to ${title}`}
          aria-expanded={composeOpen}
          onClick={() => setComposeOpen((isOpen) => !isOpen)}
        >
          {composeOpen ? '−' : '+'}
        </button>
      </div>

      {composeOpen ? (
        <form className={`inline-compose inline-compose-${composeMode}`} onSubmit={submitInlineComposer}>
          <div className="inline-compose-toolbar">
            <div className="inline-compose-mode" role="group" aria-label="New item type">
              <button
                type="button"
                className={`inline-compose-mode-button ${composeMode === 'todo' ? 'is-selected' : ''}`}
                onClick={() => {
                  setComposeMode('todo');
                  setComposeError(null);
                }}
                aria-pressed={composeMode === 'todo'}
                disabled={isSubmitting}
              >
                To-do
              </button>
              <button
                type="button"
                className={`inline-compose-mode-button ${composeMode === 'email' ? 'is-selected' : ''}`}
                onClick={() => {
                  setComposeMode('email');
                  setComposeError(null);
                }}
                aria-pressed={composeMode === 'email'}
                disabled={isSubmitting}
              >
                Email
              </button>
            </div>
            <button type="submit" className="inline-compose-submit" disabled={!canSubmit || isSubmitting}>
              {isSubmitting ? 'Saving...' : composeMode === 'email' ? 'Draft' : 'Add'}
            </button>
          </div>

          {composeError ? (
            <p className="inline-compose-error" role="alert">
              {composeError}
            </p>
          ) : null}

          {composeMode === 'todo' ? (
            <div className="inline-compose-todo">
              <div className="inline-compose-line">
                <span className="inline-compose-checkbox" aria-hidden="true" />
                <input
                  className="inline-compose-input"
                  value={todoTitle}
                  onChange={(event) => setTodoTitle(event.target.value)}
                  placeholder="New to-do"
                  autoFocus
                />
              </div>
              <input
                className="inline-compose-notes"
                value={todoNotes}
                onChange={(event) => setTodoNotes(event.target.value)}
                placeholder="Notes"
              />
            </div>
          ) : (
            <div className="inline-compose-email">
              <div className="inline-compose-recipient-row">
                <label className="inline-compose-field inline-compose-field-to">
                  <span>To</span>
                  <input
                    value={emailDraft.to}
                    onChange={(event) => updateEmailDraft('to', event.target.value)}
                    placeholder="name@email.com"
                    autoFocus
                  />
                </label>
                <div className="inline-compose-recipient-actions" aria-label="Optional recipients">
                  {showCc ? null : (
                    <button type="button" className="inline-compose-recipient-toggle" onClick={() => setShowCc(true)}>
                      Cc
                    </button>
                  )}
                  {showBcc ? null : (
                    <button type="button" className="inline-compose-recipient-toggle" onClick={() => setShowBcc(true)}>
                      Bcc
                    </button>
                  )}
                </div>
              </div>
              {showCc || showBcc ? (
                <div className="inline-compose-optional-recipients">
                  {showCc ? (
                    <label className="inline-compose-field">
                      <span>Cc</span>
                      <input value={emailDraft.cc} onChange={(event) => updateEmailDraft('cc', event.target.value)} />
                    </label>
                  ) : null}
                  {showBcc ? (
                    <label className="inline-compose-field">
                      <span>Bcc</span>
                      <input value={emailDraft.bcc} onChange={(event) => updateEmailDraft('bcc', event.target.value)} />
                    </label>
                  ) : null}
                </div>
              ) : null}
              <input
                className="inline-compose-subject"
                value={emailDraft.subject}
                onChange={(event) => updateEmailDraft('subject', event.target.value)}
                placeholder="Subject"
                aria-label="Subject"
              />
              <textarea
                className="inline-compose-body"
                value={emailDraft.body}
                onChange={(event) => updateEmailDraft('body', event.target.value)}
                placeholder="Write a note..."
                aria-label="Email body"
                rows={2}
              />
              <div className="inline-compose-email-tools">
                <button
                  type="button"
                  className={`inline-compose-tool ${emailDraft.hasAttachment ? 'is-selected' : ''}`}
                  aria-pressed={emailDraft.hasAttachment}
                  onClick={() => updateEmailDraft('hasAttachment', !emailDraft.hasAttachment)}
                  disabled={isSubmitting}
                >
                  <svg viewBox="0 0 24 24" className="inline-compose-tool-icon" aria-hidden="true">
                    <path d="M8 12.7 15.5 5.2a3.3 3.3 0 0 1 4.7 4.7l-9.4 9.4a5 5 0 0 1-7.1-7.1L13 2.9" />
                  </svg>
                  Attach
                </button>
              </div>
            </div>
          )}
        </form>
      ) : null}

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

function formatEmailDraftTitle(draft: EmailDraftState) {
  const subject = draft.subject.trim();
  const to = draft.to.trim();
  const body = draft.body.trim();
  const fallback = subject.length > 0 ? subject : body;

  if (fallback.length === 0 && to.length === 0) {
    return '';
  }

  if (to.length === 0) {
    return `Draft email: ${fallback}`;
  }

  if (fallback.length === 0) {
    return `Draft email to ${to}`;
  }

  return `Draft email to ${to}: ${fallback}`;
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

  async function completeItem() {
    if (isCompleting || checked) {
      return;
    }

    setChecked(true);
    setIsCompleting(true);

    if (!item.entityId) {
      onComplete();
      return;
    }

    try {
      await completeEntity(item.entityId);
      onComplete();
    } catch (_error) {
      setChecked(false);
    } finally {
      setIsCompleting(false);
    }
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
            void completeItem();
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
        </div>
      </div>
    </div>
  );
}

function formatDetailSourceLabel(sourceLabel: string): string {
  return sourceLabel.replace(/^\s*Sources?:\s*/i, '').trim();
}

function getDetailFactValue(
  facts: NonNullable<DashboardSectionItem['detail']>['facts'],
  label: string,
): string | undefined {
  return facts?.find((fact) => fact.label.toLowerCase() === label.toLowerCase())?.value;
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
