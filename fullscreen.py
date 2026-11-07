"""Window and full-screen setup.

The game draws a 1280x720 frame. In a window that is what is on screen
(pygame.SCALED, so F can still toggle full screen). On a television
(ZENSTATION=1 or --fullscreen) the frame is scaled once onto the display's
native resolution, keeping the aspect ratio: black bars if the panel is not
16:9. pygame's SCALED full-screen path does that scale on the GPU. If the
driver will not give a window that actually covers the panel, or a test asks
for a particular framebuffer size, the same letterbox is one CPU scale blit.
"""
import os
import sys

import pygame

# Internal frame. The television is whatever the desktop (or a test) reports.
INTERNAL = (1280, 720)


def tv_requested(argv=None, env=None):
    """True when the launcher or the command line asked for television full screen."""
    argv = sys.argv if argv is None else argv
    env = os.environ if env is None else env
    if "--fullscreen" in argv:
        return True
    return env.get("ZENSTATION", "").strip().lower() in ("1", "true", "yes", "on")


def fit_rect(src_size, dst_size):
    """Destination rect for `src_size` inside `dst_size`, aspect kept, centred.

    The result is letterboxed or pillarboxed. A 16:9 frame on a 16:9 panel
    (1080p, 4K) fills the panel exactly.
    """
    sw, sh = src_size
    dw, dh = dst_size
    if sw <= 0 or sh <= 0 or dw <= 0 or dh <= 0:
        return pygame.Rect(0, 0, max(int(dw), 1), max(int(dh), 1))
    scale = min(dw / float(sw), dh / float(sh))
    tw = min(dw, max(1, int(round(sw * scale))))
    th = min(dh, max(1, int(round(sh * scale))))
    return pygame.Rect((dw - tw) // 2, (dh - th) // 2, tw, th)


def compose(window, logical, dest=None, scaled=None):
    """One scale blit of the internal frame onto the window. Reuses `scaled`."""
    if dest is None:
        dest = fit_rect(logical.get_size(), window.get_size())
    if scaled is None or scaled.get_size() != (dest.w, dest.h):
        scaled = pygame.Surface((dest.w, dest.h))
        if pygame.display.get_surface() is not None:
            scaled = scaled.convert()
    pygame.transform.scale(logical, (dest.w, dest.h), scaled)
    if dest.w != window.get_width() or dest.h != window.get_height():
        window.fill((0, 0, 0))
    window.blit(scaled, dest.topleft)
    return scaled


class Presenter:
    """The display surface is the internal frame. pygame.SCALED presents it."""

    allows_toggle = True

    def __init__(self, screen):
        self.screen = screen

    def flip(self):
        pygame.display.flip()


class Letterbox:
    """The window is the panel; the game draws an offscreen internal frame."""

    allows_toggle = False

    def __init__(self, window, logical):
        self.screen = logical
        self.window = window
        self.dest = fit_rect(logical.get_size(), window.get_size())
        self.scaled = None

    def flip(self):
        self.scaled = compose(self.window, self.screen, self.dest, self.scaled)
        pygame.display.flip()


def _hide_cursor():
    try:
        pygame.mouse.set_visible(False)
    except pygame.error:
        pass


def open_display(size=INTERNAL, *, headless=False, tv=False, simulate=None):
    """Open the window. Returns (drawing surface, presenter).

    `simulate=(w, h)` is a framebuffer of that exact size with no border,
    used to check 1080p and 4K under the dummy video driver (which full-screens
    only at its own desktop size).
    """
    size = (int(size[0]), int(size[1]))
    if headless:
        screen = pygame.display.set_mode(size)
        return screen, Presenter(screen)
    if simulate:
        window = pygame.display.set_mode((int(simulate[0]), int(simulate[1])), pygame.NOFRAME)
        logical = pygame.Surface(size).convert()
        _hide_cursor()
        return logical, Letterbox(window, logical)
    if not tv:
        screen = pygame.display.set_mode(size, pygame.SCALED)
        return screen, Presenter(screen)

    _hide_cursor()
    # Borderless full-screen desktop. SCALED keeps the drawing surface at the
    # internal size and letterboxes it onto the panel in one present.
    flags = pygame.FULLSCREEN | pygame.SCALED | pygame.NOFRAME
    screen = pygame.display.set_mode(size, flags)
    _hide_cursor()
    desk = pygame.display.get_desktop_sizes()
    if desk:
        dw, dh = desk[0]
        ww, wh = pygame.display.get_window_size()
        covers = ww >= dw * 0.9 and wh >= dh * 0.9
        if covers and screen.get_size() == size:
            return screen, Presenter(screen)
        window = pygame.display.set_mode((dw, dh), pygame.FULLSCREEN | pygame.NOFRAME)
        logical = pygame.Surface(size).convert()
        _hide_cursor()
        return logical, Letterbox(window, logical)
    return screen, Presenter(screen)


def _close(colour, point, tol=2):
    px = colour[:3]
    return all(abs(int(a) - int(b)) <= tol for a, b in zip(px, point))


def self_check(check):
    """Exercise full screen at the dummy desktop and at simulated 1080p and 4K.

    Restores a plain 1280x720 display surface and returns it, so the game
    self-test can carry on drawing where it always has.
    """
    import time

    def pixel(surf, x, y):
        return surf.get_at((int(x), int(y)))

    # Television path: native (dummy) desktop, internal frame, cursor hidden.
    pygame.mouse.set_visible(True)
    screen, presenter = open_display(INTERNAL, tv=True)
    desk = pygame.display.get_desktop_sizes()[0]
    win = pygame.display.get_window_size()
    check("full screen fills the desktop",
          abs(win[0] - desk[0]) <= 2 and abs(win[1] - desk[1]) <= 2 and pygame.display.is_fullscreen(),
          f"window {win} desktop {desk}")
    check("full screen still draws at the internal resolution",
          screen.get_size() == INTERNAL, str(screen.get_size()))
    check("full screen hides the mouse cursor", not pygame.mouse.get_visible())
    check("driver covered the panel, so the scale is pygame's",
          isinstance(presenter, Presenter) and presenter.allows_toggle)

    ink = (10, 220, 30)
    # Simulated panels. 1080p and 4K are 16:9, so the frame goes edge to edge.
    # A 4:3 and an ultrawide panel must show bars.
    panels = (
        ((1920, 1080), False, "1080p"),
        ((3840, 2160), False, "4K"),
        ((1600, 1200), True, "4:3"),
        ((2560, 1080), True, "ultrawide"),
    )
    for panel, bars, label in panels:
        logical, presenter = open_display(INTERNAL, tv=True, simulate=panel)
        window = pygame.display.get_surface()
        check(f"{label} framebuffer is the panel size",
              window.get_size() == panel and logical.get_size() == INTERNAL and not pygame.mouse.get_visible(),
              str(window.get_size()))
        dest = presenter.dest
        expect = fit_rect(INTERNAL, panel)
        check(f"{label} letterbox rect", dest == expect, f"{dest} vs {expect}")
        if bars:
            check(f"{label} has bars", dest.x > 0 or dest.y > 0)
        else:
            check(f"{label} fills the panel", dest.x == 0 and dest.y == 0 and (dest.w, dest.h) == panel)
        logical.fill(ink)
        presenter.flip()                              # allocate the scaled buffer once
        t0 = time.perf_counter()
        presenter.flip()
        ms = (time.perf_counter() - t0) * 1000.0
        centre = pixel(window, dest.x + dest.w // 2, dest.y + dest.h // 2)
        corner = pixel(window, 0, 0)
        check(f"{label} scaled pixels",
              _close(centre, ink) and (_close(corner, (0, 0, 0)) if bars else _close(corner, ink)),
              f"centre {tuple(centre[:3])} corner {tuple(corner[:3])} scale {ms:.2f} ms")
        if bars and dest.y > 2:
            above = pixel(window, dest.x + dest.w // 2, 1)
            check(f"{label} bar is black", _close(above, (0, 0, 0)), str(tuple(above[:3])))
        if bars and dest.x > 2:
            beside = pixel(window, 1, dest.y + dest.h // 2)
            check(f"{label} pillar is black", _close(beside, (0, 0, 0)), str(tuple(beside[:3])))

    # Windowed play is unchanged: a SCALED 1280x720 window, cursor left visible.
    pygame.mouse.set_visible(True)
    screen, presenter = open_display(INTERNAL, tv=False)
    check("windowed mode stays a 1280x720 SCALED window",
          screen.get_size() == INTERNAL and pygame.display.get_window_size() == INTERNAL
          and not pygame.display.is_fullscreen() and presenter.allows_toggle and pygame.mouse.get_visible())

    # The game self-test draws straight to the display, with no scaler.
    screen = pygame.display.set_mode(INTERNAL)
    pygame.mouse.set_visible(True)
    return screen
