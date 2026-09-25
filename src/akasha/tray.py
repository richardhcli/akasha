# pyright: basic
# `pystray` ships no stubs, so every callback parameter typed from it is Unknown under `--strict`
# (like `metrics.py`'s Windows ctypes sampler). Optional code; no runtime behaviour rides on the
# annotations.
"""Optional system-tray presence for the daemon (T12.5, vision.md §7.9).

A thin UX wrapper: no daemon logic, no store access, and only ``cli/main.py``'s ``tray`` command
imports it. ``pystray``/``Pillow`` are the optional ``tray`` extra, so a plain install stays light;
importing without them raises an ordinary ``ImportError`` naming both.

``daemon.serve()`` blocks and takes no stop event (adding one is outside this task, rule 0.8), so
``run()`` starts it on a ``daemon=True`` thread and the Quit item ends the process with
``os._exit``: as abrupt as closing the console or Ctrl+C, which is today's only stop mechanism.
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
    """A generated placeholder icon (a filled circle and "tm", the neutral prefix of rule 0.6): no
    binary asset to maintain; a real one is a one-line swap.
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
        # Exit code 42 is a private contract with ``scripts/windows/run-tray-supervised.bat``
        # (T12.5): the installer's autostart runs this process in a loop that relaunches it on ANY
        # exit (Task Scheduler's own restart-on-failure proved unreliable,
        # docs/dogfood/windows-service.md) except 42, which means "the user quit". Any other code,
        # including a crash or 4 (``AlreadyRunningError``), is relaunched. ``os._exit`` rather than
        # ``sys.exit`` so no thread's cleanup can intercept or delay it.
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
