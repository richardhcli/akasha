# sandbox/init: a starting example for akasha

A folder to try transclusion in. Nothing here is special: it is just a vault.

```bash
uv tool install .                    # once, from the repository root
akasha setup sandbox/init            # the only command: starts the daemon and watches this folder
```

Within a moment `A.md` gets real ids: `^tm-new` becomes `^tm-<id>` and `{...}{tm-new}` becomes
`{...}{tm-<id>}`. Nothing else in the file changes, and no front matter is ever added.

Now make a copy and edit either one:

1. Create `B.md` and paste a line **with its id** into it (`- [ ] ship the docs ^tm-...`, or the
   braced part `{friday the 13th}{tm-...}` in the middle of a sentence, or the whole
   multi-line `{ Bring the blue bag ... }{tm-...}`).
2. Edit the text in either file (type at the end of a line, tick the checkbox, change a line of the
   multi-line span). The other file changes within about a second.
3. Copy a line into a new file **and edit it in the same save**: your edit wins and reaches every copy.
   Paste an *old* version back: it is overwritten with the current text.

```bash
akasha status          # is it running, is the token good, what is tracked
akasha review list     # only ever holds real questions (a deleted line other things depend on)
akasha down            # stop the daemon
```

Using this folder edits `A.md` (that is the point). `git checkout sandbox/init` restores the starting example.
See `docs/user/quickstart.md` for the full story.
