import configparser
import logging
import os
import pathlib
import re

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

from ks_includes.screen_panel import ScreenPanel
from ks_includes.svg_gradient import apply_filament_gradient, filament_colors_from_dict

# medusahc.current_tool: -1 means no hotend mounted, -2 means the dock sensors disagree
NO_TOOL = -1
SENSOR_ERROR = -2
EMPTY_SPOOL_COLOR = "555555"
DOT_ON_COLOR = "#4caf50"
DOT_OFF_COLOR = "#777777"
HEATING_COLOR = "#ff9800"
SPOOL_REFRESH_SECONDS = 30
STATUS_POLL_MS = 1000
SETTINGS_FILE = "MedusaHC_screen.conf"
MEDUSAHC_FIELDS = ["current_tool", "last_error", "operation", "sensors", "tool_count"]

# Stock KlipperScreen has no styles for this panel, so it brings its own.
# Literal colors keep it independent of the theme's named colors.
PANEL_CSS = b"""
.mhc-status,
.mhc-tool {
    border: .15em solid transparent;
    border-radius: .6em;
    background-color: rgba(127, 127, 127, 0.12);
    padding: .3em .5em;
}
.mhc-status.mhc-selected,
.mhc-tool.mhc-selected {
    border-color: #2196f3;
}
.mhc-status.mhc-error,
.mhc-tool.mhc-error {
    border-color: #f44336;
}
.mhc-error-text {
    color: #f44336;
}
.mhc-heat button {
    border-radius: 0;
    margin: 0;
}
.mhc-heat button:first-child {
    border-top-left-radius: .4em;
    border-bottom-left-radius: .4em;
}
.mhc-heat button:last-child {
    border-top-right-radius: .4em;
    border-bottom-right-radius: .4em;
}
"""
TOOL_MACRO_RE = re.compile(r"^gcode_macro T(\d+)$")


def compute_highlight(tools, sensors, current_tool):
    """Return (selected_tool, error_tools) from the MedusaHC hall sensors.

    Exactly one hotend out of its dock while the toolhead sensor sees it is the
    normal state. More than one out of the docks, or one out while the toolhead
    is empty, means a hotend is somewhere it should not be. Without dock sensors
    the mounted tool reported by MedusaHC is used instead.
    """

    def read(name):
        value = sensors.get(name)
        return None if value is None else int(value)

    if not any(read(f"t{tool}") is not None for tool in tools):
        return (current_tool if current_tool >= 0 else None), []

    out = [tool for tool in tools if read(f"t{tool}") == 0]
    if len(out) > 1:
        return None, out
    if not out:
        return None, []
    if read("e") == 0:
        return None, out
    return out[0], []


class Panel(ScreenPanel):
    _css_installed = False

    def __init__(self, screen, title):
        title = title or "MedusaHC"
        super().__init__(screen, title)
        self._install_css()
        # Stock KlipperScreen does not subscribe to medusahc or the T<n> macros,
        # so the panel polls them itself while it is on screen.
        self._status = {}
        self.poll_timer = None
        self.tools = list(range(int(self.medusahc.get("tool_count", 0))))
        self.spools = {}
        self.cards = {}
        self.spool_dialog = None
        self.refresh_timer = None
        self._spool_svg = None
        self._spool_pixbufs = {}

        self.status = self._build_status()
        grid = Gtk.Grid(
            row_homogeneous=True, column_homogeneous=True, row_spacing=6, column_spacing=6
        )
        columns = 2 if self._screen.vertical_mode else 3
        for tool in self.tools:
            card = self._build_card(tool)
            grid.attach(card, tool % columns, tool // columns, 1, 1)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, vexpand=True)
        main.pack_start(self.status, False, False, 0)
        if self.tools:
            main.pack_start(grid, True, True, 0)
        else:
            main.pack_start(Gtk.Label(label=_("MedusaHC reports no tools")), True, True, 0)
        self.content.add(main)
        self.update_all()

    # ------------------------------------------------------------------ data

    @classmethod
    def _install_css(cls):
        if cls._css_installed:
            return
        provider = Gtk.CssProvider()
        provider.load_from_data(PANEL_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
        )
        cls._css_installed = True

    def stat(self, name):
        return self._status.get(name) or self._printer.get_stat(name) or {}

    @property
    def medusahc(self):
        return self.stat("medusahc")

    def current_tool(self):
        try:
            return int(self.medusahc.get("current_tool", NO_TOOL))
        except (TypeError, ValueError):
            return NO_TOOL

    def sensors(self):
        return self.medusahc.get("sensors", {}) or {}

    @staticmethod
    def extruder_name(tool):
        return "extruder" if tool == 0 else f"extruder{tool}"

    def tool_spool_id(self, tool):
        return self.stat(f"gcode_macro T{tool}").get("spool_id") or None

    def tool_presets(self, tool):
        """KlipperScreen [preheat] sections that set a nozzle temperature.

        Uses the tool's own extruder value when the preset has one, else the
        generic `extruder` value. `preheat` in MedusaHC_screen.conf picks and
        orders them.
        """
        options = self._config.get_preheat_options()
        wanted = self.settings().get("medusahc", "preheat", fallback="")
        names = [name.strip() for name in wanted.split(",") if name.strip()] or list(options)
        presets = []
        for name in names:
            option = options.get(name)
            if not option:
                continue
            temperature = option.get(self.extruder_name(tool)) or option.get("extruder")
            if temperature:
                presets.append((name, temperature))
        return presets

    def settings(self):
        """MedusaHC_screen.conf next to KlipperScreen.conf.

        Kept out of KlipperScreen.conf because KlipperScreen rejects sections it
        does not know.
        """
        parser = configparser.ConfigParser()
        path = os.path.join(os.path.dirname(self._config.config_path), SETTINGS_FILE)
        try:
            parser.read(path)
        except configparser.Error:
            logging.exception(f"MedusaHC: could not read {path}")
        return parser

    def busy(self):
        return self._printer.state in ("printing", "paused") or (
            self.medusahc.get("operation", "idle") != "idle"
        )

    # ------------------------------------------------------------- widgets

    def _build_status(self):
        box = Gtk.Box(spacing=6)
        box.get_style_context().add_class("mhc-status")

        self.labels["head"] = Gtk.Label(halign=Gtk.Align.START, hexpand=True)
        self.labels["operation"] = Gtk.Label(hexpand=True)
        self.labels["mounted"] = Gtk.Label(halign=Gtk.Align.END, hexpand=True)
        self.labels["error"] = Gtk.Label(
            wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, halign=Gtk.Align.START, no_show_all=True
        )
        self.labels["error"].get_style_context().add_class("mhc-error-text")

        clean = self._gtk.Button("custom-script", None, "color3", self.bts)
        clean.connect("clicked", self.send, "CLEAN")
        drop = self._gtk.Button("arrow-up", None, "color2", self.bts)
        drop.connect("clicked", self.send, "MHC_DROP")
        for button in (clean, drop):
            button.set_hexpand(False)
            button.get_style_context().add_class("buttons_slim")
        self.control["clean"] = clean
        self.control["drop"] = drop

        row = Gtk.Box(spacing=6)
        row.pack_start(self.labels["head"], True, True, 0)
        row.pack_start(self.labels["operation"], True, True, 0)
        row.pack_start(self.labels["mounted"], True, True, 0)
        row.pack_end(drop, False, False, 0)
        row.pack_end(clean, False, False, 0)

        box.set_orientation(Gtk.Orientation.VERTICAL)
        box.pack_start(row, False, False, 0)
        box.pack_start(self.labels["error"], False, False, 0)
        return box

    def _build_card(self, tool):
        card = {}
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.get_style_context().add_class("mhc-tool")
        card["box"] = box

        select = self._gtk.Button(None, f"T{tool}", None, self.bts, Gtk.PositionType.LEFT, 1)
        select.get_style_context().add_class("buttons_slim")
        select.connect("clicked", self.send, f"T{tool}")
        card["select"] = select
        card["temperature"] = Gtk.Label(halign=Gtk.Align.END)
        top = Gtk.Box(spacing=4)
        top.pack_start(select, True, True, 0)
        top.pack_start(card["temperature"], False, False, 0)

        card["spool"] = Gtk.Image()
        spool_button = Gtk.Button(relief=Gtk.ReliefStyle.NONE, hexpand=False)
        spool_button.add(card["spool"])
        spool_button.connect("clicked", self.open_spool_dialog, tool)
        card["name"] = Gtk.Label(halign=Gtk.Align.START, ellipsize=Pango.EllipsizeMode.END)
        card["details"] = Gtk.Label(halign=Gtk.Align.START, ellipsize=Pango.EllipsizeMode.END)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        info.pack_start(card["name"], False, False, 0)
        info.pack_start(card["details"], False, False, 0)
        middle = Gtk.Box(spacing=4, vexpand=True)
        middle.pack_start(spool_button, False, False, 0)
        middle.pack_start(info, True, True, 0)

        heat = Gtk.Box(homogeneous=True)
        heat.get_style_context().add_class("mhc-heat")
        card["presets"] = []
        for name, temperature in self.tool_presets(tool):
            button = self._gtk.Button(None, name, None, self.bts, Gtk.PositionType.LEFT, 1)
            button.connect("clicked", self.heat, tool, temperature)
            heat.add(button)
            card["presets"].append((button, temperature))
        off = self._gtk.Button(None, _("Off"), None, self.bts, Gtk.PositionType.LEFT, 1)
        off.connect("clicked", self.heat, tool, 0)
        heat.add(off)
        for button in heat.get_children():
            button.get_style_context().add_class("buttons_slim")

        box.pack_start(top, False, False, 0)
        box.pack_start(middle, True, True, 0)
        box.pack_start(heat, False, False, 0)
        self.cards[tool] = card
        return box

    def spool_pixbuf(self, colors):
        key = tuple(colors)
        if key in self._spool_pixbufs:
            return self._spool_pixbufs[key]
        if self._spool_svg is None:
            root = pathlib.Path(__file__).parent.resolve().parent
            path = os.path.join(root, "styles", self._screen.theme, "images", "spool.svg")
            if not os.path.isfile(path):
                path = os.path.join(root, "styles", "spool.svg")
            self._spool_svg = pathlib.Path(path).read_text()
        height = round(self._gtk.img_scale * self.bts * 2.2)
        loader = GdkPixbuf.PixbufLoader()
        loader.set_size(round(height * 0.47), height)
        loader.write(apply_filament_gradient(self._spool_svg, list(colors)).encode())
        loader.close()
        pixbuf = loader.get_pixbuf()
        self._spool_pixbufs[key] = pixbuf
        return pixbuf

    # -------------------------------------------------------------- updates

    def update_all(self):
        selected, errors = compute_highlight(self.tools, self.sensors(), self.current_tool())
        self.update_status(selected, errors)
        for tool in self.tools:
            self.update_card(tool, selected, errors)

    def update_status(self, selected, errors):
        head = self.sensors().get("e")
        if head is None:
            self.labels["head"].set_label(_("Toolhead"))
        else:
            color = DOT_ON_COLOR if int(head) == 1 else DOT_OFF_COLOR
            label = GLib.markup_escape_text(_("Toolhead"))
            self.labels["head"].set_markup(f"<span foreground='{color}'>●</span> {label}")

        operation = self.medusahc.get("operation", "idle")
        operations = {
            "idle": _("Idle"),
            "changing": _("Changing tool"),
            "picking": _("Picking up"),
            "dropping": _("Dropping"),
        }
        self.labels["operation"].set_label(operations.get(operation, operation))

        current = self.current_tool()
        has_error = bool(errors) or current == SENSOR_ERROR
        if has_error:
            mounted = _("Unknown")
        elif selected is None:
            mounted = _("None")
        else:
            mounted = f"T{selected}"
        self.labels["mounted"].set_label(f"{_('Mounted')}: {mounted}")

        message = ""
        tools = ", ".join(f"T{tool}" for tool in errors)
        if len(errors) > 1:
            message = _("%s are out of their docks. Check the docks and the dock sensors.") % tools
        elif errors:
            message = _("%s is not in its dock and the toolhead does not detect a hotend.") % tools
        last_error = self.medusahc.get("last_error", "")
        if last_error:
            message = f"{message}\n{last_error}".strip()
        self.labels["error"].set_label(message)
        self.labels["error"].set_visible(bool(message))

        ctx = self.status.get_style_context()
        ctx.remove_class("mhc-selected")
        ctx.remove_class("mhc-error")
        if has_error or last_error:
            ctx.add_class("mhc-error")
        elif selected is not None:
            ctx.add_class("mhc-selected")

        no_tool = current < 0
        self.control["clean"].set_sensitive(not self.busy() and not no_tool)
        self.control["drop"].set_sensitive(not self.busy() and not no_tool)

    def update_card(self, tool, selected, errors):
        card = self.cards[tool]
        ctx = card["box"].get_style_context()
        ctx.remove_class("mhc-selected")
        ctx.remove_class("mhc-error")
        if tool in errors:
            ctx.add_class("mhc-error")
        elif tool == selected:
            ctx.add_class("mhc-selected")

        card["select"].set_sensitive(
            not self.busy() and tool != selected and tool != self.current_tool()
        )

        extruder = self.extruder_name(tool)
        temperature = self._printer.get_stat(extruder, "temperature")
        target = round(self._printer.get_stat(extruder, "target") or 0)
        if temperature in (None, {}):
            card["temperature"].set_label("--")
        elif target > 0:
            card["temperature"].set_markup(
                f"<span foreground='{HEATING_COLOR}'>{round(temperature)} / {target}°C</span>"
            )
        else:
            card["temperature"].set_label(f"{round(temperature)}°C")

        for button, preset_temperature in card["presets"]:
            if target > 0 and target == round(preset_temperature):
                button.get_style_context().add_class("button_active")
            else:
                button.get_style_context().remove_class("button_active")

        self.update_spool(tool)

    def update_spool(self, tool):
        card = self.cards[tool]
        spool_id = self.tool_spool_id(tool)
        spool = self.spools.get(spool_id) if spool_id is not None else None
        signature = (spool_id, spool.get("remaining_weight") if spool else None)
        if card.get("signature") == signature:
            return
        card["signature"] = signature

        if spool is None:
            colors = [EMPTY_SPOOL_COLOR]
            card["name"].set_markup(f"<span alpha='60%'>{_('No spool')}</span>")
            card["details"].set_label("--" if spool_id is None else f"#{spool_id}")
        else:
            filament = spool.get("filament", {}) or {}
            colors = filament_colors_from_dict(filament)
            card["name"].set_label(filament.get("name", f"#{spool_id}"))
            material = GLib.markup_escape_text(filament.get("material", "") or "")
            weight = spool.get("remaining_weight")
            weight_text = f"  {round(weight)} g" if weight is not None else ""
            card["details"].set_markup(f"<b>{material}</b>{weight_text}")
        card["spool"].set_from_pixbuf(self.spool_pixbuf(colors))

    def process_update(self, action, data):
        if action == "notify_active_spool_set":
            self.load_spools()
            return
        if action != "notify_status_update":
            return
        watched = {"medusahc", "print_stats"} | {self.extruder_name(tool) for tool in self.tools}
        if any(key in data for key in watched) or any(TOOL_MACRO_RE.match(k) for k in data):
            self.update_all()

    def poll_status(self):
        objects = {"medusahc": MEDUSAHC_FIELDS}
        for tool in self.tools:
            objects[f"gcode_macro T{tool}"] = ["spool_id", "color"]
        self._screen._ws.api.query_objects(objects, self._poll_status_cb)
        return True

    def _poll_status_cb(self, data, method, params):
        # Runs through GLib.idle_add: returning True would run it again forever
        status = data.get("result", {}).get("status") if isinstance(data, dict) else None
        if status:
            self._status.update(status)
            self.update_all()
        return False

    # -------------------------------------------------------------- spools

    def load_spools(self, *args):
        if not self._printer.spoolman:
            return True
        self._screen.spoolman_api.load_all_spools(callback=self._load_spools_cb)
        return True

    def _load_spools_cb(self, spools):
        if not spools:
            return False
        self.spools = {spool["id"]: spool for spool in spools}
        for tool in self.tools:
            self.cards[tool]["signature"] = None
        self.update_all()
        return False

    def open_spool_dialog(self, widget, tool):
        if not self._printer.spoolman:
            self._screen.show_popup_message(_("Spoolman is not available"))
            return
        spools = sorted(
            (spool for spool in self.spools.values() if not spool.get("archived")),
            key=lambda spool: spool["id"],
        )
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        for spool in spools:
            filament = spool.get("filament", {}) or {}
            row = Gtk.Box(spacing=8)
            row.pack_start(
                Gtk.Image.new_from_pixbuf(self.spool_pixbuf(filament_colors_from_dict(filament))),
                False,
                False,
                0,
            )
            weight = spool.get("remaining_weight")
            text = f"#{spool['id']}  {filament.get('name', '')}  {filament.get('material', '')}"
            if weight is not None:
                text += f"  {round(weight)} g"
            row.pack_start(Gtk.Label(label=text, halign=Gtk.Align.START), True, True, 0)
            button = Gtk.Button()
            button.add(row)
            button.connect("clicked", self.assign_spool, tool, spool["id"])
            listbox.add(button)

        scroll = self._gtk.ScrolledWindow()
        scroll.add(listbox)
        buttons = [
            {"name": _("Clear"), "response": Gtk.ResponseType.REJECT, "style": "dialog-warning"},
            {"name": _("Close"), "response": Gtk.ResponseType.CANCEL, "style": "dialog-default"},
        ]
        self.spool_dialog = self._gtk.Dialog(
            f"T{tool} - {_('Select spool')}", buttons, scroll, self._spool_dialog_response, tool
        )

    def _spool_dialog_response(self, dialog, response_id, tool):
        self._gtk.remove_dialog(dialog)
        self.spool_dialog = None
        if response_id == Gtk.ResponseType.REJECT:
            self._set_tool_spool(tool, None)

    def assign_spool(self, widget, tool, spool_id):
        if self.spool_dialog is not None:
            self._gtk.remove_dialog(self.spool_dialog)
            self.spool_dialog = None
        self._set_tool_spool(tool, spool_id)

    def _set_tool_spool(self, tool, spool_id):
        # Same variables Mainsail's tool spool dialog writes, so both UIs agree
        value = "None" if spool_id is None else spool_id
        self.send(None, f"SET_GCODE_VARIABLE MACRO=T{tool} VARIABLE=spool_id VALUE={value}")
        self.send(None, f"SAVE_VARIABLE VARIABLE=t{tool}__spool_id VALUE={value}")

    # ------------------------------------------------------------- actions

    def send(self, widget, gcode):
        logging.info(f"MedusaHC: {gcode}")
        self._screen._ws.api.gcode_script(gcode)

    def heat(self, widget, tool, temperature):
        self.send(
            None,
            f"SET_HEATER_TEMPERATURE HEATER={self.extruder_name(tool)} TARGET={temperature}",
        )

    def activate(self):
        self.load_spools()
        self.poll_status()
        if self.refresh_timer is None:
            self.refresh_timer = GLib.timeout_add_seconds(SPOOL_REFRESH_SECONDS, self.load_spools)
        if self.poll_timer is None:
            self.poll_timer = GLib.timeout_add(STATUS_POLL_MS, self.poll_status)
        self.update_all()

    def deactivate(self):
        for name in ("refresh_timer", "poll_timer"):
            timer = getattr(self, name)
            if timer is not None:
                GLib.source_remove(timer)
                setattr(self, name, None)
