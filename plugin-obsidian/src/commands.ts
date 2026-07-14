import { Editor } from "obsidian";

// Matches any trailing `^tm-<token>` anchor (e.g. `^tm-new` or a minted
// `^tm-<id8>`). The id8 alphabet is base32 lowercase (spec §4.1, kernel/ids.py
// alphabet "abcdefghijklmnopqrstuvwxyz234567"), not hex — so this intentionally
// matches any non-whitespace token after "^tm-" rather than re-deriving the
// exact alphabet here, to avoid drifting out of sync with the Python source
// of truth.
const EXISTING_ANCHOR_AT_EOL = /\s\^tm-\S+\s*$/;

/**
 * Append ` ^tm-new` to the line at the end of the selection (or cursor line).
 * Pure editor-buffer edit — never mints an id or calls the daemon.
 */
export default function createNodeFromSelection(editor: Editor): void {
  const from = editor.getCursor("from");
  const to = editor.getCursor("to");
  const collapsed = from.line === to.line && from.ch === to.ch;
  const pos = collapsed ? editor.getCursor() : editor.getCursor("to");
  const lineNumber = pos.line;
  const line = editor.getLine(lineNumber);

  if (EXISTING_ANCHOR_AT_EOL.test(line)) {
    return;
  }

  const trimmed = line.replace(/\s+$/, "");
  editor.setLine(lineNumber, trimmed + " ^tm-new");
}
