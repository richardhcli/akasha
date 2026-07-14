import type { Plugin } from "obsidian";

/**
 * Clipboard / anchor helpers for the Obsidian thin client (M6 / T6.5).
 *
 * Managed markdown lines end with a trailing block anchor of the form
 * `^tm-<id8>` (spec §4.7). `id8` is 8 characters from the RFC4648-lowercase
 * base32 alphabet used by `kernel/ids.py` (spec §4.1) — not hex, not UUID.
 *
 * Anchors are plain markdown text, not CodeMirror widgets. Obsidian's native
 * cut/copy/paste already retains the literal line text (including a trailing
 * anchor). This module therefore does not mint ids, does not call the daemon
 * API, and does not rewrite clipboard payloads. Its job is:
 *   1. Export pure, unit-testable matchers for the anchor grammar.
 *   2. Export `registerClipboard` as the future onload() wiring point that
 *      documents the cut→MOVE (battery E04) vs copy→E_DUP_ID (battery E05)
 *      reconcile semantics that plain-text-preserving cut/copy naturally
 *      trigger once both files are saved.
 */

/** RFC4648 lowercase base32 alphabet (spec §4.1 / kernel/ids.py `A`). */
export const ANCHOR_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567";

/**
 * Trailing minted anchor: whitespace, `^tm-`, then exactly 8 alphabet chars,
 * end of line. Does not match `^tm-new` (mint request) or mid-line text.
 */
const MINTED_ANCHOR_AT_EOL = new RegExp(
  `\\s(\\^tm-[${ANCHOR_ID_ALPHABET}]{8})\\s*$`,
);

/** Full-line match for a minted trailing anchor (capture group 1 = `^tm-<id8>`). */
const MINTED_ANCHOR_CAPTURE = new RegExp(
  `^(.*?)\\s(\\^tm-[${ANCHOR_ID_ALPHABET}]{8})\\s*$`,
);

/**
 * True iff `line` ends with a minted `^tm-<id8>` block anchor (EOL only).
 * Mid-line `^tm-…` text is plain text per §4.7 and returns false.
 */
export function lineHasAnchor(line: string): boolean {
  return MINTED_ANCHOR_AT_EOL.test(line);
}

/**
 * Return the trailing minted anchor token (`^tm-<id8>`) if present at EOL,
 * otherwise `null`. Does not match `^tm-new`.
 */
export function extractAnchor(line: string): string | null {
  const m = MINTED_ANCHOR_CAPTURE.exec(line);
  return m ? m[2] : null;
}

/**
 * Return the 8-char id from a trailing minted EOL anchor, or `null`.
 * Read-only: never mints or validates checksums client-side.
 */
export function extractAnchorId(line: string): string | null {
  const anchor = extractAnchor(line);
  if (anchor === null) {
    return null;
  }
  return anchor.slice("^tm-".length);
}

/**
 * Collect every minted EOL anchor token found in a multi-line clipboard
 * (or file) payload, in document order. Duplicate ids are listed once per
 * occurrence — useful when documenting the E05 duplicate path.
 */
export function collectAnchors(text: string): string[] {
  const out: string[] = [];
  for (const line of text.split(/\r\n|\n|\r/)) {
    const anchor = extractAnchor(line);
    if (anchor !== null) {
      out.push(anchor);
    }
  }
  return out;
}

/**
 * Register clipboard-related hooks for this plugin.
 *
 * Native Obsidian cut/copy already preserves trailing `^tm-<id8>` text, so
 * no active DOM/editor hook is required for correct paste behavior. The
 * divergent outcomes after save are entirely daemon-side:
 *   - CUT + paste into another managed file → clean MOVE (battery E04 /
 *     golden/reconcile/e04-cross-file-move/, companion e04b-s0-cross-file-move/).
 *   - COPY + paste without removing the source → two live lines with the
 *     same id → review `E_DUP_ID` (battery E05 / golden/reconcile/e05-cross-file-dup/).
 *
 * Call this from `TmHubPlugin.onload()` when a later task wires it in.
 * This task intentionally does not edit `main.ts`.
 *
 * // SPEC-QUESTION: T6.5 Files list excludes main.ts, so registerClipboard
 * // is not invoked from onload() yet. If a future task decides a non-noop
 * // hook is required (e.g. to strip anchors on copy intentionally — which
 * // would break E04/E05), wire it here and call registerClipboard(this)
 * // from main.ts. Until then this remains the documented no-op wiring point.
 */
export function registerClipboard(_plugin: Plugin): void {
  // No-op by design: plain-text anchors survive Obsidian's native clipboard.
  // `_plugin` is reserved for a future registerDomEvent / workspace hook.
  void _plugin;
}
