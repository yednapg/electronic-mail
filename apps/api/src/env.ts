import path from 'node:path';
import dotenv from 'dotenv';

dotenv.config({
  path: path.resolve(__dirname, '../.env'),
});

const port = Number(process.env.PORT ?? '3001');
const databaseUrl = process.env.DATABASE_URL;
const corsOrigin = process.env.CORS_ORIGIN ?? 'http://localhost:5173';
const googleClientId = process.env.GOOGLE_CLIENT_ID ?? '';
const googleClientSecret = process.env.GOOGLE_CLIENT_SECRET ?? '';
const googleRedirectUri =
  process.env.GOOGLE_REDIRECT_URI ?? 'http://localhost:3001/auth/google/callback';
const aiServiceUrl =
  process.env.AI_SERVICE_URL ??
  process.env.DECISION_SERVICE_URL ??
  'http://localhost:8001/decide';

if (!databaseUrl) {
  throw new Error('DATABASE_URL is required');
}

export const env = {
  port,
  databaseUrl,
  corsOrigin,
  googleClientId,
  googleClientSecret,
  googleRedirectUri,
  aiServiceUrl,
};
