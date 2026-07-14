import { Plugin } from "obsidian";
import {
  DEFAULT_SETTINGS,
  TmHubSettingTab,
  type TmHubSettings,
} from "./settings";

export default class TmHubPlugin extends Plugin {
  settings!: TmHubSettings;

  async onload(): Promise<void> {
    await this.loadSettings();
    this.addSettingTab(new TmHubSettingTab(this.app, this));
  }

  onunload(): void {}

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }

  async apiFetch(path: string, init?: RequestInit): Promise<Response> {
    return fetch(this.settings.daemonUrl + path, {
      ...init,
      headers: {
        ...(init?.headers ?? {}),
        Authorization: "Bearer " + this.settings.apiToken,
      },
    });
  }
}
