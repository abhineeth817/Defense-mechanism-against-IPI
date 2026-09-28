import time

from browsergym.core.chat import Chat


class DefenseChat(Chat):
    """BrowserGym chat with a defense toggle that does not submit a message."""

    def __init__(self, *args, initial_defense_enabled: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self._closing = False
        self.defense_enabled = initial_defense_enabled
        self.page.expose_function("set_defense_state", self._set_defense_state)
        self.page.add_script_tag(content=DEFENSE_TOGGLE_SCRIPT)
        self.page.evaluate(
            "initialEnabled => addDefenseToggle(initialEnabled)",
            self.defense_enabled,
        )

    def _set_defense_state(self, enabled: bool):
        if self._closing:
            return self.defense_enabled

        self.defense_enabled = bool(enabled)
        self.messages.append(
            {
                "role": "defense_control",
                "timestamp": time.time(),
                "message": "/defense on" if self.defense_enabled else "/defense off",
            }
        )
        return self.defense_enabled

    def close(self):
        """Stop toggle callbacks before closing the Playwright resources."""
        self._closing = True
        try:
            self.page.evaluate("window.defenseChatClosing = true;")
        except Exception:
            pass
        try:
            self.page.close()
        finally:
            self.context.close()
            self.browser.close()


DEFENSE_TOGGLE_SCRIPT = r"""
function addDefenseToggle(initialEnabled) {
    if (document.getElementById("defense-toggle")) {
        return;
    }

    const button = document.createElement("button");
    button.id = "defense-toggle";
    button.type = "button";
    button.dataset.enabled = initialEnabled ? "true" : "false";
    button.style.cssText = [
        "position: absolute",
        "top: 18px",
        "right: 18px",
        "z-index: 10",
        "padding: 8px 12px",
        "border: 1px solid #4f6c7b",
        "border-radius: 6px",
        "background: #022435",
        "color: #ffffff",
        "font: 600 12px sans-serif",
        "cursor: pointer",
    ].join(";");

    function updateButton(enabled) {
        button.textContent = enabled ? "Defense: ON" : "Defense: OFF";
        button.style.borderColor = enabled ? "#29a93e" : "#c98335";
        button.setAttribute(
            "aria-label",
            enabled ? "Turn indirect prompt injection defense off" : "Turn indirect prompt injection defense on"
        );
    }

    updateButton(initialEnabled);
    button.addEventListener("click", async () => {
        if (window.defenseChatClosing) {
            return;
        }
        const enabled = button.dataset.enabled !== "true";
        try {
            const result = await set_defense_state(enabled);
            if (!window.defenseChatClosing) {
                button.dataset.enabled = result ? "true" : "false";
                updateButton(result);
            }
        } catch (error) {
            if (!window.defenseChatClosing) {
                console.error("Unable to update defense state", error);
            }
        }
    });

    document.querySelector(".chat-container").appendChild(button);
}
"""
