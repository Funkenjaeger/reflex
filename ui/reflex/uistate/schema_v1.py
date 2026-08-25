"""UI state schema v1 -- home screen and its DRO / ELS / JOG / Index layouts.

READ ``schema.py`` FIRST. In particular: this field list and its ORDER are
frozen. Adding, removing, reordering or repurposing anything below is a new
schema id, never an edit to this one.

WHAT IS AND IS NOT HERE. Every field is an *input* to the render -- a value
something sets, which the kv rules then react to. That is what makes the picture
reproducible: the ELS banners, for instance, are pure kv expressions over
``ElsUiController`` properties (``els_advbar.kv``: ``height: dp(30) if
root.controller.takeup_warning else 0``), so capturing the property captures the
banner. Computed geometry and resolved colors are outputs and belong to the
drift digest, not here.

Where a value is already formatted for display -- ``formattedPosition``,
``instruction_text`` -- the FORMATTED form is what is captured, not the physics
behind it. It is what is literally drawn, it is smaller, and it survives changes
to format strings and unit handling.

DECLARATION ORDER IS APPLY ORDER. Use case, then screen, then mode, then
everything else: a field addressing the ELS advanced bar needs the ELS layout
mounted before it runs.
"""

from reflex.uistate.schema import Field, Kind, Schema, register

# `dispatchers/board.py` builds exactly four axes (`for i in range(4)`), so four
# fixed slots keep the field order frozen while covering every real config.
MAX_AXES = 4


# ── accessors ──────────────────────────────────────────────────────────────
#
# Each returns None when the thing it addresses does not exist in the current
# mode. `schema.snapshot` turns that into the field's default, so a DRO-mode
# snapshot does not need the ELS widgets to exist.

def _home(app):
    try:
        return app.manager.get_screen("home")
    except Exception:  # noqa: BLE001 - screen may not be mounted yet
        return None


def _els_bar(app):
    home = _home(app)
    return getattr(home, "els_bar", None) if home else None


def _els_layout(app):
    home = _home(app)
    return home.mode_layouts.get(2) if home and home.mode_layouts else None


def _els_adv_bar(app):
    layout = _els_layout(app)
    return getattr(layout, "els_adv_bar", None) if layout else None


def _spindle_info(app):
    layout = _els_layout(app)
    return getattr(layout, "spindle_info", None) if layout else None


def _jog_bar(app):
    home = _home(app)
    layout = home.mode_layouts.get(3) if home and home.mode_layouts else None
    return getattr(layout, "jog_bar", None) if layout else None


def _adv_button(app, widget_id):
    """One of the ELS advanced bar's four value buttons, by kv id."""
    bar = _els_adv_bar(app)
    return bar.ids.get(widget_id) if bar is not None else None


def _status_bar(app):
    home = _home(app)
    return getattr(home, "status_bar", None) if home else None


def _axis(app, index):
    axes = getattr(app, "axes", None) or []
    return axes[index] if index < len(axes) else None


# ── field builders ─────────────────────────────────────────────────────────

def _prop(key, kind, owner, name, default, doc="", volatile=False):
    """A field reading/writing ``name`` on whatever ``owner(app)`` returns."""
    def get(app):
        target = owner(app)
        return None if target is None else getattr(target, name)

    def apply(app, value):
        target = owner(app)
        if target is not None:
            setattr(target, name, value)
    return Field(key, kind, get, apply, default, doc, volatile)


def _readonly(key, kind, get, default, doc=""):
    """A field that describes the capture but must not be pushed back.

    Applying it would either be meaningless (the app version) or actively wrong
    (the window size, which the replay tool sets before Kivy builds a Window --
    far too early for a Field to reach).
    """
    return Field(key, kind, get, lambda app, value: None, default, doc)


def _color(key, owner, name, default="#00000000", doc=""):
    """A ColorProperty as ``#rrggbbaa`` -- compact, and legible in a decoded
    snapshot, which matters when someone is eyeballing why a replay looks off."""
    def get(app):
        target = owner(app)
        if target is None:
            return None
        rgba = getattr(target, name)
        return "#" + "".join(f"{int(round(max(0.0, min(1.0, c)) * 255)):02x}"
                             for c in list(rgba)[:4])

    def apply(app, value):
        target = owner(app)
        if target is None or not value:
            return
        text = value.lstrip("#")
        if len(text) not in (6, 8):
            return
        channels = [int(text[i:i + 2], 16) / 255.0 for i in range(0, len(text), 2)]
        if len(channels) == 3:
            channels.append(1.0)
        setattr(target, name, channels)
    return Field(key, Kind.STR, get, apply, default, doc)


_APP = lambda app: app            # noqa: E731 - table-style declaration below
_FORMATS = lambda app: app.formats  # noqa: E731
_THEME = lambda app: app.theme      # noqa: E731
_ELS = lambda app: app.els          # noqa: E731
_UIC = lambda app: app.els_uic      # noqa: E731
_SERVO = lambda app: app.servo      # noqa: E731
_BOARD = lambda app: app.board      # noqa: E731


def _axis_fields(index):
    owner = lambda app: _axis(app, index)  # noqa: E731
    p = f"ax{index}_"
    return [
        _prop(p + "name", Kind.STR, owner, "axis_name", "?"),
        _prop(p + "pos", Kind.STR, owner, "formattedPosition", "--",
              "the string actually drawn on the DRO, not the physics behind it"),
        _prop(p + "speed", Kind.STR, owner, "formattedSpeed", "--"),
        _prop(p + "pos_unit", Kind.STR, owner, "position_unit", ""),
        _prop(p + "speed_unit", Kind.STR, owner, "speed_unit", ""),
        _prop(p + "sync", Kind.BOOL, owner, "syncEnable", False),
        _prop(p + "spindle", Kind.BOOL, owner, "spindleMode", False),
    ]


FIELDS: tuple[Field, ...] = tuple([
    # ── identity / environment (never applied) ─────────────────────────────
    _readonly("app_version", Kind.STR, lambda app: app.version, "",
              "warned about at replay when it differs from the running package"),
    _readonly("win_w", Kind.UINT, lambda app: int(app.root.width), 1024),
    _readonly("win_h", Kind.UINT, lambda app: int(app.root.height), 600),

    # ── use case first: it gates which modes are allowed at all ────────────
    _prop("use_case", Kind.STR, _APP, "use_case", "lathe"),

    # ── ELS axis assignments: the ELS layout builds its bars from these, so
    #    they must land before the mode switch that mounts it ───────────────
    _prop("els_z_index", Kind.INT, _ELS, "z_axis_index", -1),
    _prop("els_x_index", Kind.INT, _ELS, "x_axis_index", -1),
    _prop("els_spindle_index", Kind.INT, _ELS, "spindle_axis_index", -1),

    # ── navigation and mode ────────────────────────────────────────────────
    Field("screen", Kind.STR,
          lambda app: app.manager.current,
          # Assigned rather than routed through `Manager.goto`, which would push
          # onto the back stack and rewrite history the capture never had.
          lambda app, value: setattr(app.manager, "current", value),
          "home"),
    Field("mode", Kind.UINT,
          lambda app: int(app.current_mode),
          lambda app, value: app.set_mode(int(value)),
          4, "1=Index 2=ELS 3=JOG 4=DRO (see popups/mode_popup.py)"),
    _prop("abs_mode", Kind.BOOL, _APP, "abs_mode", False),

    # ── theme: name first, because assigning it re-seeds the operator colors
    #    below, which must then win ─────────────────────────────────────────
    _prop("theme", Kind.STR, _FORMATS, "theme", "dark"),
    _readonly("theme_font_bold", Kind.STR, lambda app: app.theme.font_bold, ""),
    _readonly("theme_font_mono", Kind.STR, lambda app: app.theme.font_mono, ""),
    _readonly("theme_font_seg", Kind.STR, lambda app: app.theme.font_seg, ""),
    _readonly("theme_font_icon", Kind.STR, lambda app: app.theme.font_icon, "",
              "recorded so a user theme pointing outside the package is visible"),

    # ── display formats ────────────────────────────────────────────────────
    _prop("fmt_units", Kind.STR, _FORMATS, "current_format", "MM"),
    _prop("fmt_font_name", Kind.STR, _FORMATS, "font_name", "fonts/iosevka-regular.ttf"),
    _prop("fmt_font_size", Kind.UINT, _FORMATS, "font_size", 24),
    _prop("fmt_show_speeds", Kind.BOOL, _FORMATS, "show_speeds", True),
    _prop("fmt_show_wizard", Kind.BOOL, _FORMATS, "show_wizard", True),
    _prop("fmt_max_row_height", Kind.UINT, _FORMATS, "max_row_height", 150),
    _color("fmt_display_color", _FORMATS, "display_color", "#40e0edff"),
    _color("fmt_accept_color", _FORMATS, "accept_color", "#32ff32ff"),
    _color("fmt_cancel_color", _FORMATS, "cancel_color", "#ff3232ff"),
    _color("fmt_color_on", _FORMATS, "color_on", "#29d1e0ff"),
    _color("fmt_color_off", _FORMATS, "color_off", "#404d5cff"),

    # ── axes ───────────────────────────────────────────────────────────────
    _readonly("axis_count", Kind.UINT, lambda app: len(app.axes or []), 0),
]
    + [f for i in range(MAX_AXES) for f in _axis_fields(i)]
    + [
    # ── ELS bar ────────────────────────────────────────────────────────────
    _prop("els_mode_name", Kind.STR, _els_bar, "mode_name", ":("),
    _prop("els_feed_name", Kind.STR, _els_bar, "feed_name", ":("),
    _prop("els_feeds_index", Kind.UINT, _els_bar, "current_feeds_index", 0),
    _prop("els_forward", Kind.BOOL, _els_bar, "els_forward", True),
    _prop("els_advanced", Kind.BOOL, _els_bar, "enable_advanced", False),

    # ── ELS advanced bar ───────────────────────────────────────────────────
    _prop("adv_enable_stop", Kind.BOOL, _els_adv_bar, "enable_stop", True),
    _prop("adv_enable_retract", Kind.BOOL, _els_adv_bar, "enable_retract", True),
    _prop("adv_enable_wizard", Kind.BOOL, _els_adv_bar, "enable_wizard", True),
    _prop("adv_inner_thread", Kind.BOOL, _els_adv_bar, "inner_thread", False),
    _prop("adv_is_active", Kind.BOOL, _els_adv_bar, "is_active", True),
    _prop("adv_is_running", Kind.BOOL, _els_adv_bar, "is_running", False),
    _prop("adv_thread_profile", Kind.STR, _els_adv_bar, "thread_profile_type", "ISO_METRIC"),
    _prop("adv_shaft_dia", Kind.FLOAT, _els_adv_bar, "shaft_diameter", 1.0),
    _prop("adv_label_text", Kind.STR, _els_adv_bar, "label_text", ""),
    _prop("adv_display_value", Kind.STR, _els_adv_bar, "display_value", ""),
    _prop("adv_next_button_text", Kind.STR, _els_adv_bar, "next_button_text", ""),
    _prop("adv_current_state", Kind.STR, _els_adv_bar, "current_state", "idle"),
    _prop("adv_start_z_text", Kind.STR, _els_adv_bar, "start_z_text", ""),
    _prop("adv_stop_z_text", Kind.STR, _els_adv_bar, "stop_z_text", ""),
    _prop("adv_major_dia_text", Kind.STR, _els_adv_bar, "major_diameter_text", ""),
    _prop("adv_minor_dia_text", Kind.STR, _els_adv_bar, "minor_diameter_text", ""),
    _prop("adv_cutting_depth", Kind.FLOAT, _els_adv_bar, "cutting_depth", 0.0),
    _prop("adv_last_cutting_depth", Kind.FLOAT, _els_adv_bar, "last_cutting_depth", 0.0),
    _prop("adv_material_width", Kind.FLOAT, _els_adv_bar, "material_width", 0.0),

    # ── ELS spindle readout ────────────────────────────────────────────────
    _prop("spindle_rpm", Kind.STR, _spindle_info, "spindle_rpm", "--"),
    # `display_rpm`, not just `spindle_rpm`: the kv draws the former (DSEG7 has
    # no "+" glyph, so it renders a sign-stripped copy), and a same-value write
    # to `spindle_rpm` fires no binding, leaving the drawn string untouched.
    _prop("spindle_display_rpm", Kind.STR, _spindle_info, "display_rpm", "--"),
    # ICON_STOP from els_mode_layout.py, spelled as an escape rather than the
    # literal private-use glyph so it survives a diff and an editor intact.
    _prop("spindle_icon", Kind.STR, _spindle_info, "direction_icon",
          "\\uf04d"),

    # ── ELS UI controller: the ELS screen is very nearly a pure function of
    #    these, banners and blinking fields included ────────────────────────
    _prop("uic_state", Kind.STR, _UIC, "ui_state", "idle"),
    _prop("uic_instruction", Kind.STR, _UIC, "instruction_text", ""),
    _prop("uic_action_text", Kind.STR, _UIC, "action_button_text", ""),
    _prop("uic_alarm_text", Kind.STR, _UIC, "alarm_text", ""),
    _prop("uic_active_input", Kind.STR, _UIC, "active_input", ""),
    _prop("uic_takeup_warning", Kind.STR, _UIC, "takeup_warning", ""),
    _prop("uic_reframe_message", Kind.STR, _UIC, "reframe_message", ""),
    _prop("uic_stop_z", Kind.FLOAT, _UIC, "stop_z", 0.0),
    _prop("uic_retract_z", Kind.FLOAT, _UIC, "retract_z", 0.0),
    _prop("uic_start_dia", Kind.FLOAT, _UIC, "start_dia", 0.0),
    _prop("uic_stop_dia", Kind.FLOAT, _UIC, "stop_dia", 0.0),
    _prop("uic_stop_z_error", Kind.STR, _UIC, "stop_z_error", ""),
    _prop("uic_retract_z_error", Kind.STR, _UIC, "retract_z_error", ""),
    _prop("uic_start_dia_error", Kind.STR, _UIC, "start_dia_error", ""),
    _prop("uic_stop_dia_error", Kind.STR, _UIC, "stop_dia_error", ""),
    _prop("uic_stop_z_valid", Kind.BOOL, _UIC, "stop_z_valid", False),
    _prop("uic_retract_z_valid", Kind.BOOL, _UIC, "retract_z_valid", False),
    _prop("uic_start_dia_valid", Kind.BOOL, _UIC, "start_dia_valid", False),
    _prop("uic_stop_dia_valid", Kind.BOOL, _UIC, "stop_dia_valid", False),
    _prop("uic_in_cycle", Kind.BOOL, _UIC, "in_cycle", False),
    _prop("uic_engaged", Kind.BOOL, _UIC, "engaged", False),
    _prop("uic_stop_active", Kind.BOOL, _UIC, "els_stop_active", False),
    _prop("uic_start_stop_enabled", Kind.BOOL, _UIC, "start_stop_enabled", False),
    _prop("uic_start_not_stop", Kind.BOOL, _UIC, "start_not_stop", False),
    _prop("uic_action_allowed", Kind.BOOL, _UIC, "action_allowed", True),
    _prop("uic_x_z_inputs_enabled", Kind.BOOL, _UIC, "x_z_inputs_enabled", False),
    _prop("uic_retract_enabled", Kind.BOOL, _UIC, "retract_enabled", False),
    _prop("uic_wizard_enabled", Kind.BOOL, _UIC, "wizard_enabled", False),
    _prop("uic_els_forward", Kind.BOOL, _UIC, "els_forward", True),
    _prop("uic_is_threading", Kind.BOOL, _UIC, "is_threading", False),
    _prop("uic_is_inner", Kind.BOOL, _UIC, "is_inner", False),
    _prop("uic_depth_reached", Kind.BOOL, _UIC, "depth_reached", False),
    _prop("uic_reframed_warn", Kind.BOOL, _UIC, "targets_reframed_warn", False),
    _prop("uic_reframe_confirm", Kind.BOOL, _UIC, "reframe_confirm_pending", False),

    # ── blink phase of the ELS value buttons ───────────────────────────────
    #
    # `active_input` above says WHICH button blinks; these say what phase it was
    # caught in. `TextHeaderButton._blink` drives both the label color and the
    # border width (text_header_button.kv:16,52-55), so without them a replay
    # renders the attention cue at an arbitrary phase and the drift guard
    # reports it -- on the very frames an operator most wants to look at.
    _prop("blink_stop_z", Kind.BOOL,
          lambda app: _adv_button(app, "btn_stop_z"), "_blink", False),
    _prop("blink_start_z", Kind.BOOL,
          lambda app: _adv_button(app, "btn_start_z"), "_blink", False),
    _prop("blink_major_dia", Kind.BOOL,
          lambda app: _adv_button(app, "btn_major_dia"), "_blink", False),
    _prop("blink_minor_dia", Kind.BOOL,
          lambda app: _adv_button(app, "btn_minor_dia"), "_blink", False),

    # ── jog / servo ────────────────────────────────────────────────────────
    _prop("jog_desired_speed", Kind.FLOAT, _jog_bar, "desired_speed", 0.0),
    _prop("jog_enable", Kind.BOOL, _jog_bar, "enable_jog", False),
    _prop("jog_enable_reverse", Kind.BOOL, _jog_bar, "enable_jog_reverse", False),
    _prop("servo_mode", Kind.UINT, _SERVO, "servoMode", 0),

    # ── status bar ─────────────────────────────────────────────────────────
    #
    # interval / fps / cycles are all DRAWN (statusbar.kv:47-59 renders each as
    # a LedButton label), so they are applied, not merely recorded -- otherwise
    # every replayed frame would differ from its capture and the drift guard
    # would cry wolf on every single one. `StatusBar.update` is suppressed under
    # `app.replay_mode` so its 5 Hz timer cannot overwrite them mid-replay.
    _prop("board_connected", Kind.BOOL, _BOARD, "connected", False),
    # volatile: a 4 Hz timer toggles this forever (board.blinker), and a 5 Hz
    # one rewrites fps. Both are drawn, so both are captured and applied -- but
    # neither is evidence that the UI is still settling.
    _prop("board_blink", Kind.BOOL, _BOARD, "blink", False, volatile=True),
    _prop("status_interval", Kind.UINT, _status_bar, "interval", 0),
    _prop("status_cycles", Kind.UINT, _status_bar, "cycles", 0),
    # FLOAT, not UINT: the kv renders "{:0.0f}".format(root.fps), so truncating
    # 59.7 to 59 at capture draws "59" where the machine drew "60" -- a one-pixel
    # lie that the drift guard would report on literally every frame.
    _prop("status_fps", Kind.FLOAT, _status_bar, "fps", 0.0, volatile=True),
])


SCHEMA_V1 = register(Schema(
    id=1,
    name="home-dro-els-v1",
    fields=FIELDS,
))
