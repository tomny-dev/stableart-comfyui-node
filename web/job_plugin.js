import { app } from "../../scripts/app.js";

// Registers the StableArt Job Node configuration in ComfyUI's Settings panel.
// ComfyUI persists these values server-side in user/<user>/comfy.settings.json,
// which the Python plugin reads at startup (config.py). Precedence is
// env var > config.toml > these settings > default, so power users can still
// override via the environment. Because the broker connection is established
// once when ComfyUI starts, changing a value here takes effect on the next
// ComfyUI restart.
app.registerExtension({
  name: "stableart.job-plugin",
  settings: [
    {
      id: "stableart.brokerUrl",
      name: "Broker URL",
      category: ["StableArt Job Node", "Connection", "Broker URL"],
      type: "text",
      defaultValue: "https://broker.stableart.io",
      tooltip: "Node broker base URL (http/https, auto-upgraded to ws/wss). Restart ComfyUI to apply.",
    },
    {
      id: "stableart.apiKey",
      name: "Gateway API key",
      category: ["StableArt Job Node", "Connection", "API key"],
      type: "text",
      defaultValue: "",
      tooltip: "Operator API key (owner/admin). Stored locally in comfy.settings.json. Restart ComfyUI to apply.",
    },
    {
      id: "stableart.nodeName",
      name: "Node name",
      category: ["StableArt Job Node", "Identity", "Node name"],
      type: "text",
      defaultValue: "",
      tooltip: "Label shown in the StableArt dashboard. Restart ComfyUI to apply.",
    },
  ],
});
