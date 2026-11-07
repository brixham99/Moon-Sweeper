"""SDL game controller support for Moon Sweeper.

Xbox and PlayStation pads share one layout via the SDL GameController API
(A/Cross, B/Circle, X/Square, Y/Triangle, Start/Options, Back/Share). The
keyboard is never rewritten: a connected pad only adds to it, and an idle
pad leaves the key dict exactly as it was.

There is no local two-player mode. The first pad to be connected drives
Jetman; a second pad is remembered so it can take over if the first is
unplugged, and holding Back/Share on either pad quits to the launcher.
"""
import pygame

DEADZONE = 0.3
QUIT_HOLD = 1.0          # seconds Back/Share must be held to leave the game
MENU_REPEAT_DELAY = 0.32
MENU_REPEAT_EVERY = 0.09
MENU_THRESHOLD = 0.25    # on the rescaled stick, so a light tilt does not edit a name

# Face / system buttons. D-pad and shoulders are read alongside these.
_EDGE_BUTTONS = (
    ("confirm", pygame.CONTROLLER_BUTTON_A),
    ("cancel", pygame.CONTROLLER_BUTTON_B),
    ("fire", pygame.CONTROLLER_BUTTON_X),
    ("extra", pygame.CONTROLLER_BUTTON_Y),
    ("pause", pygame.CONTROLLER_BUTTON_START),
)
_MENU_NAMES = ("menu_left", "menu_right", "menu_up", "menu_down")
_AXES = (
    pygame.CONTROLLER_AXIS_LEFTX,
    pygame.CONTROLLER_AXIS_LEFTY,
    pygame.CONTROLLER_AXIS_RIGHTX,
    pygame.CONTROLLER_AXIS_RIGHTY,
    pygame.CONTROLLER_AXIS_TRIGGERLEFT,
    pygame.CONTROLLER_AXIS_TRIGGERRIGHT,
)
_BUTTONS = [b for _, b in _EDGE_BUTTONS] + [
    pygame.CONTROLLER_BUTTON_BACK,
    pygame.CONTROLLER_BUTTON_LEFTSHOULDER,
    pygame.CONTROLLER_BUTTON_RIGHTSHOULDER,
    pygame.CONTROLLER_BUTTON_DPAD_UP,
    pygame.CONTROLLER_BUTTON_DPAD_DOWN,
    pygame.CONTROLLER_BUTTON_DPAD_LEFT,
    pygame.CONTROLLER_BUTTON_DPAD_RIGHT,
]


def apply_deadzone(value, deadzone=DEADZONE):
    """Map a -1..1 axis through a deadzone, rescaled so full tilt is still 1."""
    if value is None:
        return 0.0
    x = max(-1.0, min(1.0, float(value)))
    if abs(x) < deadzone:
        return 0.0
    sign = 1.0 if x > 0.0 else -1.0
    return sign * (abs(x) - deadzone) / (1.0 - deadzone)


def _norm_axis(raw):
    return max(-1.0, min(1.0, float(raw) / 32767.0))


class _Pad:
    def __init__(self, instance_id):
        self.instance_id = instance_id
        self.controller = None
        self.buttons = {}
        self.axes = {}
        self.prev_buttons = {}
        self.primed = False
        self.detached = False

    def sample(self):
        """Refresh from the device. Virtual pads (no device) keep event state."""
        c = self.controller
        if c is None:
            return
        try:
            if hasattr(c, "attached") and not c.attached():
                self.detached = True
                return
            for b in _BUTTONS:
                self.buttons[b] = bool(c.get_button(b))
            for a in _AXES:
                self.axes[a] = _norm_axis(c.get_axis(a))
        except pygame.error:
            self.detached = True


class Pads:
    def __init__(self):
        self.by_id = {}
        self.order = []          # connection order; index 0 drives the game
        self._edges = []
        self.back_held = 0.0
        self.menu_hold = {}
        self.menu_suppress = set()
        self.menu_pad = None
        self._ctl = None
        try:
            from pygame._sdl2 import controller as ctl
            self._ctl = ctl
            if not ctl.get_init():
                ctl.init()
            self.scan()
        except Exception:
            self._ctl = None

    # ------------------------------------------------------------- devices
    def scan(self):
        ctl = self._ctl
        if ctl is None:
            return
        try:
            n = ctl.get_count()
        except pygame.error:
            return
        for i in range(n):
            try:
                if ctl.is_controller(i):
                    self._open(i)
            except pygame.error:
                continue

    def _open(self, index):
        ctl = self._ctl
        if ctl is None:
            return
        try:
            if not ctl.is_controller(index):
                return
            c = ctl.Controller(index)
        except pygame.error:
            return
        iid = c.id
        pad = self.by_id.get(iid)
        if pad is None:
            pad = _Pad(iid)
            self.by_id[iid] = pad
            self.order.append(iid)
        pad.controller = c
        pad.detached = False

    def _ensure(self, instance_id):
        """A pad mentioned by an event. Used for hot-plug and for tests."""
        if instance_id is None:
            instance_id = 0
        pad = self.by_id.get(instance_id)
        if pad is None:
            pad = _Pad(instance_id)
            self.by_id[instance_id] = pad
            self.order.append(instance_id)
        return pad

    def _drop(self, instance_id):
        pad = self.by_id.pop(instance_id, None)
        if instance_id in self.order:
            self.order.remove(instance_id)
        if pad is not None and pad.controller is not None:
            try:
                pad.controller.quit()
            except pygame.error:
                pass

    def connected(self):
        return bool(self.order)

    def player(self, index=0):
        if index < 0 or index >= len(self.order):
            return None
        return self.by_id.get(self.order[index])

    def event(self, e):
        """Feed one pygame event. True if it was a controller event."""
        t = e.type
        if t == pygame.CONTROLLERDEVICEADDED:
            self._open(getattr(e, "device_index", 0))
            return True
        if t == pygame.CONTROLLERDEVICEREMOVED:
            self._drop(getattr(e, "instance_id", None))
            return True
        if t == pygame.CONTROLLERDEVICEREMAPPED:
            return True
        if t in (pygame.CONTROLLERBUTTONDOWN, pygame.CONTROLLERBUTTONUP):
            pad = self._ensure(getattr(e, "instance_id", 0))
            # A real device is sampled each frame; events cover virtual pads
            # and the gap before the first sample.
            if pad.controller is None:
                pad.buttons[e.button] = t == pygame.CONTROLLERBUTTONDOWN
            return True
        if t == pygame.CONTROLLERAXISMOTION:
            pad = self._ensure(getattr(e, "instance_id", 0))
            if pad.controller is None:
                pad.axes[e.axis] = _norm_axis(getattr(e, "value", 0))
            return True
        return False

    # ------------------------------------------------------------- frame
    def update(self, dt):
        """Poll devices. Returns 'quit' when Back/Share has been held long enough."""
        self._edges = []
        for iid in list(self.order):
            pad = self.by_id.get(iid)
            if pad is None:
                continue
            pad.sample()
            if pad.detached:
                self._drop(iid)
        self._button_and_menu_edges(dt)
        if self._quit_hold(dt):
            return "quit"
        return None

    def edges(self):
        return list(self._edges)

    def _quit_hold(self, dt):
        held = any(p.buttons.get(pygame.CONTROLLER_BUTTON_BACK) for p in self.by_id.values())
        if not held:
            self.back_held = 0.0
            return False
        self.back_held += dt
        if self.back_held >= QUIT_HOLD:
            self.back_held = 0.0
            return True
        return False

    def _button_and_menu_edges(self, dt):
        pad = self.player(0)
        pid = pad.instance_id if pad is not None else None
        if pid != self.menu_pad:
            self.menu_pad = pid
            self.menu_hold.clear()
            self.menu_suppress.clear()
        if pad is None:
            return
        if not pad.primed:
            pad.primed = True
            pad.prev_buttons = dict(pad.buttons)
            self.menu_suppress = set(self._menu_dirs(pad))
            return
        for name, button in _EDGE_BUTTONS:
            if pad.buttons.get(button) and not pad.prev_buttons.get(button):
                self._edges.append(name)
        pad.prev_buttons = dict(pad.buttons)
        self._menu_repeat(pad, dt)

    def _menu_dirs(self, pad):
        """Digital directions for name entry, from the D-pad or the left stick."""
        dirs = set()
        if pad.buttons.get(pygame.CONTROLLER_BUTTON_DPAD_LEFT):
            dirs.add("menu_left")
        if pad.buttons.get(pygame.CONTROLLER_BUTTON_DPAD_RIGHT):
            dirs.add("menu_right")
        if pad.buttons.get(pygame.CONTROLLER_BUTTON_DPAD_UP):
            dirs.add("menu_up")
        if pad.buttons.get(pygame.CONTROLLER_BUTTON_DPAD_DOWN):
            dirs.add("menu_down")
        if dirs:
            return dirs
        x = apply_deadzone(pad.axes.get(pygame.CONTROLLER_AXIS_LEFTX, 0.0))
        y = apply_deadzone(pad.axes.get(pygame.CONTROLLER_AXIS_LEFTY, 0.0))
        # One axis at a time, so a diagonal does not move and change letter together.
        if abs(x) >= abs(y) and abs(x) >= MENU_THRESHOLD:
            dirs.add("menu_left" if x < 0 else "menu_right")
        elif abs(y) > abs(x) and abs(y) >= MENU_THRESHOLD:
            dirs.add("menu_up" if y < 0 else "menu_down")
        return dirs

    def _menu_repeat(self, pad, dt):
        active = self._menu_dirs(pad)
        for name in _MENU_NAMES:
            if name not in active:
                self.menu_hold[name] = 0.0
                self.menu_suppress.discard(name)
                continue
            if name in self.menu_suppress:
                continue
            t0 = self.menu_hold.get(name, 0.0)
            t1 = t0 + dt
            self.menu_hold[name] = t1
            if t0 <= 0.0:
                self._edges.append(name)
            elif _repeat_slots(t1) > _repeat_slots(t0):
                self._edges.append(name)

    def gameplay(self):
        """Held tank controls for the first pad, or None when no pad is connected.

        Values are 0..1. The stick is analogue; the D-pad is full deflection.
        """
        pad = self.player(0)
        if pad is None:
            return None
        x = apply_deadzone(pad.axes.get(pygame.CONTROLLER_AXIS_LEFTX, 0.0))
        y = apply_deadzone(pad.axes.get(pygame.CONTROLLER_AXIS_LEFTY, 0.0))
        if pad.buttons.get(pygame.CONTROLLER_BUTTON_DPAD_LEFT):
            x = -1.0
        if pad.buttons.get(pygame.CONTROLLER_BUTTON_DPAD_RIGHT):
            x = 1.0
        if pad.buttons.get(pygame.CONTROLLER_BUTTON_DPAD_UP):
            y = -1.0
        if pad.buttons.get(pygame.CONTROLLER_BUTTON_DPAD_DOWN):
            y = 1.0
        trigger_r = apply_deadzone(max(0.0, pad.axes.get(pygame.CONTROLLER_AXIS_TRIGGERRIGHT, 0.0)))
        trigger_l = apply_deadzone(max(0.0, pad.axes.get(pygame.CONTROLLER_AXIS_TRIGGERLEFT, 0.0)))
        fire = 1 if (pad.buttons.get(pygame.CONTROLLER_BUTTON_X)
                     or pad.buttons.get(pygame.CONTROLLER_BUTTON_RIGHTSHOULDER)
                     or trigger_r > 0.0) else 0
        jet = 1 if (pad.buttons.get(pygame.CONTROLLER_BUTTON_A) or trigger_l > 0.0) else 0
        return {
            "left": max(0.0, -x),
            "right": max(0.0, x),
            "up": max(0.0, -y),
            "down": max(0.0, y),
            "jet": jet,
            "fire": fire,
        }

    def apply(self, keys, move=None):
        """Merge pad holds into a keyboard dict. An idle or absent pad returns `keys` itself."""
        if move is None:
            move = self.gameplay()
        if not move:
            return keys
        if not any(move[k] for k in ("left", "right", "up", "down", "jet", "fire")):
            return keys
        out = dict(keys)
        for k in ("left", "right", "up", "down", "jet", "fire"):
            out[k] = max(keys.get(k, 0) or 0, move[k])
        return out


def _repeat_slots(t):
    if t < MENU_REPEAT_DELAY:
        return 0
    return 1 + int((t - MENU_REPEAT_DELAY) / MENU_REPEAT_EVERY)


# ---------------------------------------------------------------- hints
# British English. Shown on the title screen and the pause panel, and on the
# name-entry line (that screen has to say how to spell a name). Gameplay itself
# keeps its top status line and has no bottom hint bar.

def control_lines(pad):
    if not pad:
        return [
            "Left / Right: turn      Up / Down: forwards / backwards      Space: jetpack",
            "Ctrl or Z: fire      B: drop the bomb      P: pause      F: full screen      Esc: quit",
            "Carry the bomb from the rover to the Alien Missile Base and drop it on the dome.",
            "Watch your stamina: aliens bite chunks out of it. Rest on the rover roof to recover.",
        ]
    return [
        "Stick or D-pad: turn and move      A / Cross: jetpack      X / Square: fire",
        "B / Circle or Y / Triangle: drop the bomb      Start / Options: pause      Hold Back / Share: quit",
        "Carry the bomb from the rover to the Alien Missile Base and drop it on the dome.",
        "Watch your stamina: aliens bite chunks out of it. Rest on the rover roof to recover.",
    ]


def title_prompt(pad):
    return "Press A / Cross to start" if pad else "Press Enter to start"


def pause_lines(pad):
    if not pad:
        return ["P or Enter to carry on      Esc to quit to the title screen"]
    return [
        "A / Cross or Start / Options to carry on",
        "B / Circle to quit to the title screen",
    ]


def entry_heading(pad):
    if pad:
        return "You made the Top 10! Enter your name:"
    return "You made the Top 10! Type your name:"


def entry_hint(pad, name_max):
    if pad:
        return "Up or Down: letter      Left or Right: move      A / Cross: save      B / Circle: skip      X / Square: delete"
    return f"Up to {name_max} letters or numbers.   Enter to save, Esc to skip"


def over_prompt(pad):
    if pad:
        return "Press A / Cross to return to the title screen"
    return "Press Enter to return to the title screen"


def self_check():
    """Headless checks with synthetic controller events. Yields (name, ok, info)."""
    pygame.joystick.init()
    out = []

    def add(name, ok, info=""):
        out.append((name, bool(ok), info))

    # deadzone
    add("deadzone swallows a small tilt", apply_deadzone(0.2) == 0.0 and apply_deadzone(-0.29) == 0.0)
    mid = apply_deadzone(0.5)
    add("deadzone rescales the rest of the stick", abs(mid - (0.2 / 0.7)) < 1e-6, f"{mid:.4f}")
    add("full tilt is still full", apply_deadzone(1.0) == 1.0 and apply_deadzone(-1.0) == -1.0)

    def axis_event(instance, axis, value):
        return pygame.event.Event(pygame.CONTROLLERAXISMOTION, axis=axis, value=value, instance_id=instance)

    def button_event(kind, button, instance):
        return pygame.event.Event(kind, button=button, instance_id=instance)

    down = pygame.CONTROLLERBUTTONDOWN
    up = pygame.CONTROLLERBUTTONUP

    pads = Pads()
    pads.update(0.016)                       # nothing connected yet
    keys = {"left": 1, "right": 0, "up": 0, "down": 0, "jet": 0, "fire": 0}
    add("no pad leaves the keyboard dict untouched", pads.apply(keys) is keys)

    # player 1 stick, after the pad has been primed
    pads.event(button_event(down, pygame.CONTROLLER_BUTTON_A, 1))
    pads.update(0.016)                       # prime: the held A must not confirm
    add("a button held as the pad appears does not confirm", "confirm" not in pads.edges())
    pads.event(button_event(up, pygame.CONTROLLER_BUTTON_A, 1))
    pads.update(0.016)
    pads.event(button_event(down, pygame.CONTROLLER_BUTTON_A, 1))
    pads.update(0.016)
    add("A presses confirm once", pads.edges() == ["confirm"] or pads.edges() == ["confirm"], str(pads.edges()))
    move = pads.gameplay()
    add("A holds the jetpack", move is not None and move["jet"] == 1 and move["fire"] == 0, str(move))

    pads.event(axis_event(1, pygame.CONTROLLER_AXIS_LEFTX, int(0.2 * 32767)))
    pads.update(0.016)
    move = pads.gameplay()
    add("stick inside the deadzone does not turn", move["left"] == 0 and move["right"] == 0, str(move))
    idle = {"left": 0, "right": 0, "up": 0, "down": 0, "jet": 0, "fire": 0}
    # jet is still held from A, so apply must merge — release A first
    pads.event(button_event(up, pygame.CONTROLLER_BUTTON_A, 1))
    pads.event(axis_event(1, pygame.CONTROLLER_AXIS_LEFTX, 0))
    pads.update(0.016)
    add("idle pad still returns the same keyboard dict", pads.apply(idle) is idle)

    pads.event(axis_event(1, pygame.CONTROLLER_AXIS_LEFTX, 32767))
    pads.event(axis_event(1, pygame.CONTROLLER_AXIS_LEFTY, -32767))
    pads.update(0.016)
    move = pads.gameplay()
    pads.event(axis_event(1, pygame.CONTROLLER_AXIS_TRIGGERRIGHT, int(0.2 * 32767)))
    pads.update(0.016)
    add("a resting trigger does not fire", pads.gameplay()["fire"] == 0)
    pads.event(axis_event(1, pygame.CONTROLLER_AXIS_TRIGGERRIGHT, 32767))
    pads.event(button_event(down, pygame.CONTROLLER_BUTTON_RIGHTSHOULDER, 1))
    pads.update(0.016)
    add("right trigger fires", pads.gameplay()["fire"] == 1)
    pads.event(axis_event(1, pygame.CONTROLLER_AXIS_TRIGGERRIGHT, 0))
    pads.event(button_event(up, pygame.CONTROLLER_BUTTON_RIGHTSHOULDER, 1))
    pads.event(axis_event(1, pygame.CONTROLLER_AXIS_TRIGGERLEFT, 32767))
    pads.update(0.016)
    add("left trigger is the jetpack as well as A", pads.gameplay()["jet"] == 1 and pads.gameplay()["fire"] == 0)
    pads.event(axis_event(1, pygame.CONTROLLER_AXIS_TRIGGERLEFT, 0))
    pads.update(0.016)

    add("full stick is turn-right and forwards",
        abs(move["right"] - 1) < 1e-6 and abs(move["up"] - 1) < 1e-6 and move["left"] == 0 and move["down"] == 0,
        str(move))
    merged = pads.apply(idle)
    add("pad merge keeps every keyboard field", set(merged) == set(idle) and merged["right"] == 1 and merged is not idle)

    # D-pad overrides the stick on that axis
    pads.event(button_event(down, pygame.CONTROLLER_BUTTON_DPAD_LEFT, 1))
    pads.update(0.016)
    move = pads.gameplay()
    add("D-pad left overrides a stick pushed right", move["left"] == 1 and move["right"] == 0, str(move))
    pads.event(button_event(up, pygame.CONTROLLER_BUTTON_DPAD_LEFT, 1))

    # second pad must not drive player 1
    pads.event(axis_event(2, pygame.CONTROLLER_AXIS_LEFTX, -32767))
    pads.update(0.016)
    move = pads.gameplay()
    add("the second pad does not drive Jetman", move["left"] == 0 and pads.player(1) is not None, str(move))

    # hot-plug away
    pads.event(pygame.event.Event(pygame.CONTROLLERDEVICEREMOVED, instance_id=1))
    add("unplugging the first pad hands the game to the second",
        pads.connected() and pads.player(0).instance_id == 2)
    move = pads.gameplay()
    add("the remaining pad is now player 1", move["left"] > 0.9, str(move))
    pads.event(pygame.event.Event(pygame.CONTROLLERDEVICEREMOVED, instance_id=2))
    add("unplugging the last pad clears it", not pads.connected() and pads.gameplay() is None and pads.apply(idle) is idle)

    # quit hold, on a fresh pad
    pads.event(button_event(down, pygame.CONTROLLER_BUTTON_BACK, 7))
    pads.update(0.016)                       # prime
    add("a tap of Back does not quit", pads.update(0.4) is None)
    add("holding Back for about a second quits", pads.update(0.7) == "quit")
    pads.event(button_event(up, pygame.CONTROLLER_BUTTON_BACK, 7))
    pads.update(0.016)
    pads.event(button_event(down, pygame.CONTROLLER_BUTTON_BACK, 7))
    add("releasing Back starts the hold again", pads.update(0.5) is None)
    pads.event(button_event(up, pygame.CONTROLLER_BUTTON_BACK, 7))
    pads.update(0.016)

    # name-entry repeat on the D-pad: one edge, then a repeat, not a stream
    pads.event(button_event(down, pygame.CONTROLLER_BUTTON_DPAD_UP, 7))
    pads.update(0.016)
    n_first = pads.edges().count("menu_up")
    pads.update(0.05)
    n_early = pads.edges().count("menu_up")
    pads.update(MENU_REPEAT_DELAY)
    n_later = pads.edges().count("menu_up")
    add("D-pad up cycles once, then repeats", n_first == 1 and n_early == 0 and n_later >= 1,
        f"first {n_first} early {n_early} later {n_later}")

    # hints
    kb = " ".join(control_lines(False))
    pd = " ".join(control_lines(True))
    add("keyboard hints stay the written ones",
        "Left / Right: turn" in kb and "Space: jetpack" in kb and "Cross" not in kb and "Esc: quit" in kb)
    add("pad hints name both Xbox and PlayStation",
        "A / Cross" in pd and "B / Circle" in pd and "Start / Options" in pd and "Back / Share" in pd
        and "Enter" not in title_prompt(True) and "Enter" in title_prompt(False))
    add("pause hint switches with the pad",
        "Esc" in pause_lines(False)[0] and "Circle" in " ".join(pause_lines(True)) and "Esc" not in " ".join(pause_lines(True)))
    return out
