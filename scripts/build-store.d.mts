export interface StoreConfig {
  apiOrigin: string;
  extensionId: string;
}
export function storeConfig(env: Record<string, string | undefined>): StoreConfig;
export function verifyBackend(config: StoreConfig, fetcher?: typeof fetch): Promise<void>;
