# Notifications

Sigil separates in-app alerts from desktop notifications. In the terminal
client, transient alerts appear at the top right, above the composer, without
taking keyboard focus. Long stacks scroll within the upper part of the screen
instead of covering the input or question panel.

Desktop notifications announce a completed turn, a failed turn, or a batch of
questions waiting for your answer. A turn that ends while a background subagent
keeps working is not announced on its own: the terminal client holds the alert
until the last subagent stops, which is normally the turn that reads its report.
Cancelling a turn does not send a completion notification. Each notification
identifies the project and conversation title; these names can be visible on a
locked screen. The message is generic and does not include prompts, answers,
question text, or exception details. If the session has no title yet, the
notification uses its short ID instead of message content.

- **Linux terminal:** Install `notify-send` (usually the `libnotify-bin` or
  `libnotify` package). Delivery needs a running desktop notification service.
- **macOS terminal:** Sigil uses `osascript`; allow notifications for the host
  application in system settings if required.
- **Other terminal platforms:** Native desktop delivery is not implemented.
  In-app alerts and question panels still work.
- **Web desktop:** Select **Enable alerts** in the header and grant notification
  permission. HTTPS or localhost and a supported browser are required. Select
  **Alerts on** to disable delivery again. This preference belongs to the
  browser. The page must remain open and connected; this is not background push
  delivery. Many mobile browsers require a service worker and cannot use this
  delivery path.

Terminal desktop notifications are enabled by default. Use `/alerts` to check
whether they are enabled and whether a local helper is available. Use
`/alerts on` or `/alerts off` to change the preference immediately, including
during a turn. The preference is saved in `~/.sigil/ui.json` (or under
`SIGIL_HOME`).

Terminal delivery runs on the machine hosting Sigil, not on an SSH client's
machine. It is best-effort and runs off the UI thread with a three-second
process timeout. Missing helpers, denied permissions, and system notification
policies can prevent an alert without interrupting the turn. Desktop delivery is
disabled during headless UI tests. Browser event replay and duplicate sequence
numbers do not resend old desktop alerts.

In the browser, answering a question, starting another turn, disabling alerts,
returning focus to the app, or signing out closes the obsolete desktop alert.
The button follows permission changes and preferences changed in another tab;
while a permission request is pending, it cannot open another request.

---

[Documentation index](../../README.md) · [User documentation](../README.md)
