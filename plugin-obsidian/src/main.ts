import { Plugin } from "obsidian";
import createNodeFromSelection from "./commands";
import {
  DEFAULT_SETTINGS,
  TmHubSettingTab,
  type TmHubSettings,
} from "./settings";
import { StatusBar } from "./statusbar";

export default class TmHubPlugin extends Plugin {
  settings!: TmHubSettings;

  async onload(): Promise<void> {
    await this.loadSettings();
    this.addSettingTab(new TmHubSettingTab(this.app, this));

    this.addCommand({
      id: "tm-create-node-from-selection",
      name: "Create node from selection",
      editorCallback: (editor) => createNodeFromSelection(editor),
    });

    const statusBar = new StatusBar(this, this.addStatusBarItem());
    await statusBar.refresh();
    this.registerInterval(
      window.setInterval(() => {
        void statusBar.refresh();
      }, 5000),
    );
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
