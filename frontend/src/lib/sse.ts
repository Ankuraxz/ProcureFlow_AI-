import type { ParsedSSEEvent, SSEEventType } from '../types/procureflow';

const VALID_SSE_TYPES = new Set<SSEEventType>([
  'agent_status',
  'skill_activated',
  'text_delta',
  'text_done',
  'a2ui_surface',
  'hitl_required',
  'done',
  'error',
]);

function isSSEEventType(value: unknown): value is SSEEventType {
  return typeof value === 'string' && VALID_SSE_TYPES.has(value as SSEEventType);
}

/**
 * Parses a single raw SSE event block (lines separated by `\n`) into a typed `ParsedSSEEvent`.
 * Supports both named `event: <type>` headers and JSON-embedded `type` / `event` fields.
 */
export function parseSSEBlock(rawBlock: string): ParsedSSEEvent | null {
  const lines = rawBlock.split(/\r?\n/);
  let explicitEvent: string | null = null;
  const dataLines: string[] = [];

  for (const line of lines) {
    if (!line || line.startsWith(':')) {
      continue;
    }
    if (line.startsWith('event:')) {
      explicitEvent = line.slice('event:'.length).trim();
    } else if (line.startsWith('data:')) {
      const value = line.slice('data:'.length);
      dataLines.push(value.startsWith(' ') ? value.slice(1) : value);
    }
  }

  if (dataLines.length === 0 && !explicitEvent) {
    return null;
  }

  const rawData = dataLines.join('\n').trim();
  if (rawData === '[DONE]') {
    return { type: 'done', payload: {} };
  }

  let parsedPayload: Record<string, unknown> = {};
  if (rawData) {
    try {
      const candidate = JSON.parse(rawData) as unknown;
      if (candidate && typeof candidate === 'object' && !Array.isArray(candidate)) {
        parsedPayload = candidate as Record<string, unknown>;
      } else {
        parsedPayload = { delta: String(candidate ?? '') };
      }
    } catch {
      // Plain-text data frame; treat as text delta unless explicit event says otherwise
      parsedPayload =
        explicitEvent === 'error' ? { message: rawData } : { delta: rawData };
    }
  }

  const resolvedEvent =
    explicitEvent ||
    (typeof parsedPayload.type === 'string' ? parsedPayload.type : null) ||
    (typeof parsedPayload.event === 'string' ? parsedPayload.event : null);

  if (isSSEEventType(resolvedEvent)) {
    // Unwrap nested `data` or `payload` envelope if present alongside `type`/`event`,
    // while preserving top-level keys so downstream handlers can inspect either shape.
    if (
      parsedPayload.data &&
      typeof parsedPayload.data === 'object' &&
      !Array.isArray(parsedPayload.data)
    ) {
      return {
        type: resolvedEvent,
        payload: {
          ...parsedPayload,
          ...(parsedPayload.data as Record<string, unknown>),
        },
      };
    }
    return {
      type: resolvedEvent,
      payload: parsedPayload,
    };
  }

  // Fallback heuristics if backend emits unnamed JSON chunks
  if ('surface_type' in parsedPayload || 'surfaceType' in parsedPayload) {
    return { type: 'a2ui_surface', payload: parsedPayload };
  }
  if ('delta' in parsedPayload || 'text' in parsedPayload) {
    return { type: 'text_delta', payload: parsedPayload };
  }

  return null;
}

export interface StreamSSEOptions {
  url: string;
  body: Record<string, unknown>;
  signal?: AbortSignal;
  onEvent: (event: ParsedSSEEvent) => void;
}

/**
 * Initiates a POST request and streams Server-Sent Events via `fetch` + `ReadableStream`.
 * Also handles JSON fallback if an endpoint responds with `application/json` instead of `text/event-stream`.
 */
export async function postSSEStream({
  url,
  body,
  signal,
  onEvent,
}: StreamSSEOptions): Promise<void> {
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream, application/json',
    },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    let errorDetail = `HTTP ${response.status} ${response.statusText}`;
    try {
      const errJson = (await response.json()) as Record<string, unknown>;
      if (typeof errJson.detail === 'string') {
        errorDetail = errJson.detail;
      } else if (typeof errJson.error === 'string') {
        errorDetail = errJson.error;
      } else if (errJson.detail) {
        errorDetail = JSON.stringify(errJson.detail);
      }
    } catch {
      const errText = await response.text().catch(() => '');
      if (errText) {
        errorDetail = errText;
      }
    }
    throw new Error(errorDetail);
  }

  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    const json = (await response.json()) as Record<string, unknown>;
    if (typeof json.reply === 'string' || typeof json.message === 'string') {
      onEvent({
        type: 'text_done',
        payload: { text: (json.reply as string) || (json.message as string) },
      });
    }
    if (Array.isArray(json.a2ui_surfaces)) {
      for (const surface of json.a2ui_surfaces) {
        if (surface && typeof surface === 'object') {
          onEvent({
            type: 'a2ui_surface',
            payload: surface as Record<string, unknown>,
          });
        }
      }
    }
    onEvent({ type: 'done', payload: json });
    return;
  }

  if (!response.body) {
    throw new Error('ReadableStream body is unavailable on SSE response.');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });

      // SSE events are delimited by blank lines (`\n\n` or `\r\n\r\n`)
      let boundaryMatch = buffer.match(/\r?\n\r?\n/);
      while (boundaryMatch && boundaryMatch.index !== undefined) {
        const rawBlock = buffer.slice(0, boundaryMatch.index);
        buffer = buffer.slice(boundaryMatch.index + boundaryMatch[0].length);
        const parsed = parseSSEBlock(rawBlock);
        if (parsed) {
          onEvent(parsed);
        }
        boundaryMatch = buffer.match(/\r?\n\r?\n/);
      }
    }

    buffer += decoder.decode();
    if (buffer.trim()) {
      const parsed = parseSSEBlock(buffer);
      if (parsed) {
        onEvent(parsed);
      }
    }
  } finally {
    reader.releaseLock();
  }
}
