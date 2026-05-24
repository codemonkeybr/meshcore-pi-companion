import type { RepeaterLoginResponse } from '../types';

export type ServerLoginMethod = 'password' | 'blank';

export type ServerLoginAttemptState =
  | {
      method: ServerLoginMethod;
      outcome: 'confirmed';
      summary: string;
      details: string | null;
      heardBack: true;
      at: number;
    }
  | {
      method: ServerLoginMethod;
      outcome: 'not_confirmed';
      summary: string;
      details: string | null;
      heardBack: boolean;
      at: number;
    }
  | {
      method: ServerLoginMethod;
      outcome: 'request_failed';
      summary: string;
      details: string | null;
      heardBack: false;
      at: number;
    };

function getServerLoginMethodLabel(
  method: ServerLoginMethod,
  blankLabel = 'existing-access'
): string {
  return method === 'password' ? 'password' : blankLabel;
}

export function getServerLoginAttemptTone(
  attempt: ServerLoginAttemptState | null
): 'success' | 'warning' | 'destructive' | 'muted' {
  if (!attempt) return 'muted';
  if (attempt.outcome === 'confirmed') return 'success';
  if (attempt.outcome === 'not_confirmed') return 'warning';
  return 'destructive';
}

export function buildServerLoginAttemptFromResponse(
  method: ServerLoginMethod,
  result: RepeaterLoginResponse,
  entityLabel: string
): ServerLoginAttemptState {
  const methodLabel = getServerLoginMethodLabel(method);
  const at = Date.now();
  const target = `the ${entityLabel}`;

  if (result.authenticated) {
    return {
      method,
      outcome: 'confirmed',
      summary: `Login confirmed by ${target}.`,
      details: null,
      heardBack: true,
      at,
    };
  }

  if (result.status === 'timeout') {
    return {
      method,
      outcome: 'not_confirmed',
      summary: `We couldn't confirm the login.`,
      details:
        result.message ??
        `No confirmation came back from ${target} after the ${methodLabel} login attempt.`,
      heardBack: false,
      at,
    };
  }

  return {
    method,
    outcome: 'not_confirmed',
    summary: `Login was not confirmed.`,
    details:
      result.message ??
      `${target} responded, but did not confirm the ${methodLabel} login attempt.`,
    heardBack: true,
    at,
  };
}

export function buildServerLoginAttemptFromError(
  method: ServerLoginMethod,
  message: string,
  entityLabel: string
): ServerLoginAttemptState {
  const methodLabel = getServerLoginMethodLabel(method);
  const target = `the ${entityLabel}`;
  return {
    method,
    outcome: 'request_failed',
    summary: `We couldn't send the login request.`,
    details: `${target} never acknowledged the ${methodLabel} login attempt. ${message}`,
    heardBack: false,
    at: Date.now(),
  };
}
