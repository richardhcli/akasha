import { App, PluginSettingTab, Setting } from "obsidian";
import type TmHubPlugin from "./main";

export interface TmHubSettings {
  daemonUrl: string;
  apiToken: string;
}

export const DEFAULT_SETTINGS: TmHubSettings = {
  daemonUrl: "http://127.0.0.1:7433",
  apiToken: "",
};

export class TmHubSettingTab extends PluginSettingTab {
  plugin: TmHubPlugin;

  constructor(app: App, plugin: TmHubPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();

    new Setting(containerEl)
      .setName("Daemon URL")
      .setDesc("Base URL of the local tm daemon")
      .addText((text) =>
        text
          .setPlaceholder("http://127.0.0.1:7433")
          .setValue(this.plugin.settings.daemonUrl)
          .onChange(async (value) => {
            this.plugin.settings.daemonUrl = value;
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("API token")
      .setDesc("Bearer token for daemon API requests")
      .addText((text) => {
        text.inputEl.type = "password";
        text
          .setPlaceholder("token")
          .setValue(this.plugin.settings.apiToken)
          .onChange(async (value) => {
            this.plugin.settings.apiToken = value;
            await this.plugin.saveSettings();
          });
      });
  }
}
