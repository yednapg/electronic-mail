import fs from 'node:fs';
import path from 'node:path';

import { google } from 'googleapis';
import type { calendar_v3, gmail_v1 } from 'googleapis';
import type { Credentials } from 'google-auth-library';
import type { PrismaClient, Prisma } from '@prisma/client';
import type { SourceRecord } from '@electronic-mail/types';

import { prisma } from '../db';
import { env } from '../env';

const GMAIL_SCOPE = 'https://www.googleapis.com/auth/gmail.readonly';
const CALENDAR_SCOPE = 'https://www.googleapis.com/auth/calendar.readonly';
const GOOGLE_SCOPES = [GMAIL_SCOPE, CALENDAR_SCOPE];
const TOKEN_FILE_PATH = path.resolve(__dirname, '../../.google-oauth.json');
const DEV_USER_ID = 'google-dev-user';
const GMAIL_MAX_RESULTS = 100;
const CALENDAR_MAX_RESULTS = 20;
const CALENDAR_WINDOW_DAYS = 7;

type StoredGoogleTokens = {
  access_token?: string | null;
  refresh_token?: string | null;
  scope?: string | null;
  token_type?: string | null;
  expiry_date?: number | null;
};

export function getGoogleAuthUrl(): string {
  const auth = createOAuthClient();

  if (auth === null) {
    throw new Error('Google OAuth is not configured');
  }

  return auth.generateAuthUrl({
    access_type: 'offline',
    prompt: 'consent',
    scope: GOOGLE_SCOPES,
  });
}

export async function handleGoogleCallback(code: string): Promise<void> {
  const auth = createOAuthClient();

  if (auth === null) {
    throw new Error('Google OAuth is not configured');
  }

  const { tokens } = await auth.getToken(code);

  auth.setCredentials(tokens);
  saveTokens(tokens);
}

export async function fetchGoogleSourceRecords(): Promise<SourceRecord[]> {
  const auth = createAuthorizedClient();

  if (auth === null) {
    return [];
  }

  const [gmailRecords, calendarRecords] = await Promise.all([
    fetchRecentGmailRecords(auth),
    fetchUpcomingCalendarRecords(auth),
  ]);
  const records = [...gmailRecords, ...calendarRecords];

  await persistSourceRecords(records);

  return records.sort((left, right) =>
    right.received_at.localeCompare(left.received_at),
  );
}

export async function persistSourceRecords(
  records: SourceRecord[],
  client = prisma,
): Promise<void> {
  await Promise.all(
    records.map((record) =>
      client.sourceRecord.upsert({
        where: { id: record.id },
        create: {
          id: record.id,
          source: record.source,
          threadId: record.thread_id.length > 0 ? record.thread_id : null,
          subject: getRawPayloadValue(record.raw_payload, 'subject'),
          sender:
            getRawPayloadValue(record.raw_payload, 'from') ??
            getRawPayloadValue(record.raw_payload, 'sender'),
          timestamp: new Date(record.received_at),
          rawPayload: record.raw_payload as Prisma.InputJsonValue,
        },
        update: {
          source: record.source,
          threadId: record.thread_id.length > 0 ? record.thread_id : null,
          subject: getRawPayloadValue(record.raw_payload, 'subject'),
          sender:
            getRawPayloadValue(record.raw_payload, 'from') ??
            getRawPayloadValue(record.raw_payload, 'sender'),
          timestamp: new Date(record.received_at),
          rawPayload: record.raw_payload as Prisma.InputJsonValue,
        },
      }),
    ),
  );
}

export function isGoogleConfigured(): boolean {
  return env.googleClientId.length > 0 && env.googleClientSecret.length > 0;
}

export function hasStoredGoogleTokens(): boolean {
  return loadStoredTokens() !== null;
}

function createOAuthClient() {
  if (!isGoogleConfigured()) {
    return null;
  }

  return new google.auth.OAuth2(
    env.googleClientId,
    env.googleClientSecret,
    env.googleRedirectUri,
  );
}

function createAuthorizedClient() {
  const auth = createOAuthClient();
  const tokens = loadStoredTokens();

  if (auth === null || tokens === null) {
    return null;
  }

  auth.setCredentials(toCredentials(tokens));
  auth.on('tokens', (nextTokens) => {
    saveTokens({
      ...tokens,
      ...nextTokens,
      refresh_token: nextTokens.refresh_token ?? tokens.refresh_token,
    });
  });

  return auth;
}

function loadStoredTokens(): StoredGoogleTokens | null {
  if (!fs.existsSync(TOKEN_FILE_PATH)) {
    return null;
  }

  try {
    const raw = fs.readFileSync(TOKEN_FILE_PATH, 'utf8');
    return JSON.parse(raw) as StoredGoogleTokens;
  } catch {
    return null;
  }
}

function saveTokens(tokens: StoredGoogleTokens): void {
  fs.writeFileSync(TOKEN_FILE_PATH, JSON.stringify(tokens, null, 2));
}

function toCredentials(tokens: StoredGoogleTokens): Credentials {
  return {
    ...(tokens.access_token != null ? { access_token: tokens.access_token } : {}),
    ...(tokens.refresh_token != null ? { refresh_token: tokens.refresh_token } : {}),
    ...(tokens.scope != null ? { scope: tokens.scope } : {}),
    ...(tokens.token_type != null ? { token_type: tokens.token_type } : {}),
    ...(tokens.expiry_date != null ? { expiry_date: tokens.expiry_date } : {}),
  };
}

async function fetchRecentGmailRecords(
  auth: InstanceType<typeof google.auth.OAuth2>,
): Promise<SourceRecord[]> {
  const gmail = google.gmail({ version: 'v1', auth });
  const listed = await gmail.users.messages.list({
    userId: 'me',
    maxResults: GMAIL_MAX_RESULTS,
    labelIds: ['INBOX', 'UNREAD'],
  });

  const messages = listed.data.messages ?? [];
  const detailedMessages = await Promise.all(
    messages.map(async (message) => {
      if (!message.id) {
        return null;
      }

      const full = await gmail.users.messages.get({
        userId: 'me',
        id: message.id,
        format: 'full',
      });

      return toGmailSourceRecord(full.data);
    }),
  );

  return detailedMessages.filter((record): record is SourceRecord => record !== null);
}

async function fetchUpcomingCalendarRecords(
  auth: InstanceType<typeof google.auth.OAuth2>,
): Promise<SourceRecord[]> {
  const calendar = google.calendar({ version: 'v3', auth });
  const timeMin = new Date().toISOString();
  const timeMax = new Date(Date.now() + CALENDAR_WINDOW_DAYS * 24 * 60 * 60 * 1000).toISOString();
  const listed = await calendar.events.list({
    calendarId: 'primary',
    singleEvents: true,
    orderBy: 'startTime',
    timeMin,
    timeMax,
    maxResults: CALENDAR_MAX_RESULTS,
  });

  return (listed.data.items ?? [])
    .map((event) => toCalendarSourceRecord(event))
    .filter((record): record is SourceRecord => record !== null);
}

function toGmailSourceRecord(message: gmail_v1.Schema$Message): SourceRecord | null {
  const payload = message.payload;
  const headers = payload?.headers ?? [];
  const subject = getHeader(headers, 'subject')?.trim() ?? '';
  const sender = getHeader(headers, 'from')?.trim() ?? '';
  const recipients = getHeader(headers, 'to')?.trim() ?? '';
  const dateHeader = getHeader(headers, 'date')?.trim() ?? '';
  const internalDate = message.internalDate ? new Date(Number(message.internalDate)).toISOString() : null;
  const receivedAt = toValidIso(dateHeader) ?? internalDate;
  const body = extractGmailBody(payload).trim() || message.snippet?.trim() || '';

  if (!message.id || !message.threadId || !receivedAt || subject.length === 0) {
    return null;
  }

  return {
    id: message.id,
    user_id: DEV_USER_ID,
    source: 'gmail',
    thread_id: message.threadId,
    raw_payload: {
      subject,
      body,
      from: sender,
      to: recipients,
      received_at: receivedAt,
    },
    received_at: receivedAt,
  };
}

function toCalendarSourceRecord(
  event: calendar_v3.Schema$Event,
): SourceRecord | null {
  const subject = event.summary?.trim() ?? '';
  const body = event.description?.trim() ?? '';
  const start = event.start?.dateTime ?? event.start?.date ?? null;
  const end = event.end?.dateTime ?? event.end?.date ?? null;
  const receivedAt = toValidIso(start) ?? toValidIso(event.updated) ?? null;
  const attendees = (event.attendees ?? [])
    .map((attendee) => attendee.email?.trim() ?? '')
    .filter((email) => email.length > 0);
  const organizer =
    event.organizer?.email?.trim() ??
    event.creator?.email?.trim() ??
    'calendar@google.com';

  if (
    !event.id ||
    !receivedAt ||
    subject.length === 0
  ) {
    return null;
  }

  return {
    id: event.id,
    user_id: DEV_USER_ID,
    source: 'calendar',
    thread_id: event.id,
    raw_payload: {
      subject,
      body,
      from: organizer,
      attendees,
      all_day: event.start?.dateTime === undefined,
      start,
      end,
    },
    received_at: receivedAt,
  };
}

function getHeader(
  headers: gmail_v1.Schema$MessagePartHeader[] | undefined,
  name: string,
): string | undefined {
  return headers?.find((header) => header.name?.toLowerCase() === name.toLowerCase())?.value ?? undefined;
}

function extractGmailBody(
  payload: gmail_v1.Schema$MessagePart | undefined,
): string {
  if (!payload) {
    return '';
  }

  if (payload.mimeType === 'text/plain' && payload.body?.data) {
    return decodeBase64Url(payload.body.data);
  }

  for (const part of payload.parts ?? []) {
    const extracted = extractGmailBody(part);

    if (extracted.length > 0) {
      return extracted;
    }
  }

  if (payload.body?.data) {
    return decodeBase64Url(payload.body.data);
  }

  return '';
}

function decodeBase64Url(value: string): string {
  return Buffer.from(value.replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString('utf8');
}

function toValidIso(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }

  const parsed = Date.parse(value);

  if (Number.isNaN(parsed)) {
    return null;
  }

  return new Date(parsed).toISOString();
}

function getRawPayloadValue(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];

  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : null;
}
