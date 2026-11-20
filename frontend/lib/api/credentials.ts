// api keys live in this tab's memory only; a reload asks for the key again instead of persisting a secret
let apiKey: string | null = null;

export function setApiKey(key: string | null): void {
  apiKey = key?.trim() || null;
}

export function getApiKey(): string | null {
  return apiKey;
}
