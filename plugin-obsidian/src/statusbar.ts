import type TmHubPlugin from "./main";

interface SyncRootStatus {
  violations?: unknown[];
}

interface UnresolvedEntry {
  bucket?: string;
}

interface SyncStatusResponse {
  sync_roots?: SyncRootStatus[];
  unresolved?: UnresolvedEntry[];
}

function countViolations(data: SyncStatusResponse): number {
  let count = 0;
  for (const root of data.sync_roots ?? []) {
    count += root.violations?.length ?? 0;
  }
  for (const entry of data.unresolved ?? []) {
    if (entry.bucket === "violations") {
      count += 1;
    }
  }
  return count;
}

export class StatusBar {
  private readonly plugin: TmHubPlugin;
  private readonly el: HTMLElement;

  constructor(plugin: TmHubPlugin, el: HTMLElement) {
    this.plugin = plugin;
    this.el = el;
    this.el.setText("TM: …");
  }

  async refresh(): Promise<void> {
    try {
      const response = await this.plugin.apiFetch("/v1/sync/status");
      if (!response.ok) {
        this.el.setText("TM: offline");
        return;
      }
      const data = (await response.json()) as SyncStatusResponse;
      const violations = countViolations(data);
      if (violations === 0) {
        this.el.setText("TM: synced · 0 violations");
      } else {
        this.el.setText(`TM: ${violations} violations`);
      }
    } catch (err) {
      console.debug("TM status bar refresh failed", err);
      this.el.setText("TM: offline");
    }
  }
}
