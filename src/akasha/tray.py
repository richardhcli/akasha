# pyright: basic
# `pystray` ships no type stubs/py.typed marker, so every callback parameter
# typed from it (icon/item below) is unavoidably Unknown under `--strict`
# (reportMissingTypeStubs/reportUnknownParameterType etc.) -- same class of
# gap as `metrics.py`'s Windows ctypes/psapi sampler (T9.2). Downgrading only
# THIS file to pyright's `basic` mode (not touching `[tool.pyright]`'s
# project-wide `strict` in pyproject.toml, and not affecting any other
# module) is the narrowest fix; `tray.py` is optional/extra code (see
# docstring below) with no runtime behavior riding on these particular
# annotations.
"""Optional system-tray presence for the daemon (build-plan T12.5, vision.md
Sec7.9: "tray presence").

A thin UX wrapper only -- adds no daemon logic, never touches
kernel/store.py, and is never imported by anything else under
``src/akasha`` (only ``cli/main.py``'s ``tray`` command reaches it, and only
when that one command actually runs). ``pystray``/``Pillow`` are an optional
extra (``pyproject.toml``'s ``[project.optional-dependencies] tray``), not a
core dependency, so a plain CLI/API install stays exactly as light as it was
before this module existed -- importing this module without them installed
raises a normal ``ImportError`` with the two package names in the message,
not a bespoke one, since that is already unambiguous.

``daemon.serve()`` is blocking (``uvicorn.run`` inside it never returns
until process shutdown) and has no external stop-event parameter -- adding
one would be a change to ``daemon.py`` itself, out of this task's Files list
(build-plan rule 0.8). So ``run()`` below starts it on a background
``daemon=True`` thread and the tray's Quit menu item ends the process
directly (``os._exit``) rather than joining a graceful shutdown -- this is
NOT a regression versus today's only stop mechanism (closing the console
window / Ctrl+C), which is equally abrupt; it is the same behavior with a
tray icon in front of it instead of a console window.
"""

from __future__ import annotations

import os
import threading
import webbrowser
from typing import TYPE_CHECKING

from akasha import daemon as daemon_module

if TYPE_CHECKING:
    from akasha.config import Config


def _icon_image() -> object:
    """A small generated placeholder icon -- no binary asset to keep in sync.

    Kept intentionally simple (a filled circle + "tm", the same neutral
    on-disk prefix build-plan rule 0.6 already uses elsewhere) rather than
    shipping a .ico file that would need separate maintenance; swapping in a
    real designed icon later is a one-line change here, not a rebuild-plan
    task.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=(30, 100, 200, 255))
    draw.text((16, 22), "tm", fill=(255, 255, 255, 255))
    return img


def run(config: Config) -> None:
    """Start the daemon on a background thread; block on the tray icon's event loop.

    Menu: open the web UI (default action, i.e. also fires on a tray-icon
    double-click), open the config/log folder in File Explorer, and quit.
    """
    import pystray

    base_url = f"http://{config.bind}:{config.port}"
    startup_error: list[BaseException] = []

    def _serve() -> None:
        # A thread target's exception never propagates to the thread that
        # started it -- it just prints to stderr and the thread dies
        # silently. Capture it here so `run()` can re-raise it on the
        # calling thread below (in particular AlreadyRunningError, which
        # `cli/main.py`'s `tray` command needs to catch the same way it
        # already catches it from the foreground `daemon` command).
        try:
            daemon_module.serve(config)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread just below, never swallowed
            startup_error.append(exc)

    server_thread = threading.Thread(target=_serve, name="akasha-daemon", daemon=True)
    server_thread.start()

    # daemon.serve()'s single-instance lock is acquired near-instantly and
    # never blocks (single_instance_lock's own docstring), so a brief join
    # window is enough to catch a same-machine second-launch conflict before
    # committing to the tray icon's blocking event loop below -- otherwise a
    # second `akasha tray` would silently show a working-looking icon with
    # no server behind it, instead of failing the same clean way a second
    # `akasha daemon` already does.
    server_thread.join(timeout=2.0)
    if startup_error:
        raise startup_error[0]
    if not server_thread.is_alive():
        raise RuntimeError("akasha daemon thread exited unexpectedly during startup")

    def _config_dir() -> str:
        if config.path is not None:
            from pathlib import Path

            return str(Path(config.path).parent)
        from akasha.config import default_config_dir

        return str(default_config_dir())

    # `pystray` is imported inside this function (see module comment above),
    # so it is a local name, not a module-level one -- pyright rejects using
    # a local name in a type annotation (reportInvalidTypeForm) even under
    # `basic` mode. These three callbacks match pystray's own
    # `Callable[[Icon, MenuItem], None]` menu-action signature at runtime;
    # left unannotated here rather than fought with a local-import type.
    def open_ui(icon, item) -> None:
        webbrowser.open(f"{base_url}/dashboard")

    def open_folder(icon, item) -> None:
        os.startfile(_config_dir())  # type: ignore[attr-defined]  # Windows-only, matches daemon.py's msvcrt precedent

    def quit_app(icon, item) -> None:
        # Exit code 42 is a private contract with
        # scripts/windows/run-tray-supervised.bat (build-plan T12.5): the
        # installer's autostart entry runs this process under a supervisor
        # loop that unconditionally relaunches it on ANY exit (the only
        # empirically reliable crash-recovery mechanism on this host --
        # Task Scheduler's own restart-on-failure was proven unreliable,
        # see docs/dogfood/windows-service.md) -- EXCEPT exit code 42,
        # which the .bat treats as "the user asked to quit, stop looping"
        # rather than a crash to recover from. Any other exit code
        # (including a genuine crash, or 4 from a same-machine
        # AlreadyRunningError conflict) is treated as unplanned and gets
        # relaunched. `os._exit` (not `sys.exit`) so the tray/daemon
        # threads can never intercept or delay this with their own
        # cleanup -- matches this module's existing "abrupt exit is not a
        # regression" precedent (see module docstring).
        icon.stop()
        os._exit(42)

    # Label deliberately stays plain "Quit", not "...until next sign-in":
    # this module has no way to know whether it's running under the
    # installer's supervisor loop (where exit code 42 above means "stay
    # down until next logon") or invoked directly (`uv run akasha tray` /
    # `akasha.exe tray` with no supervisor), where Quit really is final.
    # Documented per-context in docs/user/ops/autostart.md instead of
    # guessed at here.
    menu = pystray.Menu(
        pystray.MenuItem("Open akasha", open_ui, default=True),
        pystray.MenuItem("Open config/logs folder", open_folder),
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon("akasha", _icon_image(), "akasha daemon", menu)
    icon.run()
