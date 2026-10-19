# MedusaHC KlipperScreen Add-On

A [KlipperScreen](https://github.com/KlipperScreen/KlipperScreen) panel for the
[MedusaHC](https://github.com/francislz/MedusaHC) hotend changer. It works with stock
KlipperScreen: nothing in the KlipperScreen repository is modified.

## What it shows

- **Status bar**: toolhead hall sensor, current operation (idle, picking up, dropping),
  the mounted tool and any MedusaHC error, plus buttons to clean the nozzle (`CLEAN`)
  and drop the tool (`MHC_DROP`).
- **One card per tool** (3 columns, 2 on a portrait screen):
  - a `T<n>` button to change to that tool, with the hotend temperature next to it
  - the Spoolman spool assigned to the tool: reel in the filament color, name,
    material and remaining weight. Tap the reel to pick a different spool.
  - preheat buttons from KlipperScreen's preheat presets, plus Off
- **Outlines from the dock sensors**: blue for the hotend that is out of its dock while
  the toolhead sensor detects it; red for every hotend out of its dock when more than
  one is out, or when one is out and the toolhead is empty.

The spool of each tool is stored in the same variables Mainsail's tool spool dialog
uses (`T<n>.spool_id` and `t<n>__spool_id` in `save_variables`), so both interfaces
always agree.

The menu entry only appears on printers whose Klipper config has a `[medusahc]`
section, so a KlipperScreen that controls several printers shows it only where it
applies. It is listed in the main menu and in the print control menu.

## Requirements

- KlipperScreen with Moonraker access to a printer running MedusaHC
- Optional: Moonraker's `[spoolman]` component, for the spool information
- `T<n>` macros with `variable_spool_id: None` if you want per-tool spools

## Install

On the machine running KlipperScreen:

```bash
cd ~
git clone https://github.com/francislz/medusahc-klipper-screen-add-on.git
cd medusahc-klipper-screen-add-on
./install.sh -r
```

The installer:

1. links `KlipperScreen/medusahc.py` into `~/KlipperScreen/panels/` and tells git to
   ignore it
2. links `MedusaHC_menu.conf` next to `KlipperScreen.conf` and adds
   `[include MedusaHC_menu.conf]` to it
3. copies `MedusaHC_screen.conf` next to `KlipperScreen.conf` if it is not there yet
4. restarts KlipperScreen (`-r`)

Run `./install.sh -h` for the options (other config or KlipperScreen paths).

## Update

```bash
cd ~/medusahc-klipper-screen-add-on
git pull
sudo systemctl restart KlipperScreen
```

## Settings

`MedusaHC_screen.conf`, next to `KlipperScreen.conf`:

```ini
[medusahc]
# Preheat buttons on each tool card, in this order
preheat: PLA, ABS
```

The temperatures come from KlipperScreen's preheat presets in `KlipperScreen.conf`.
A preset can set a different temperature per tool with `extruder1`, `extruder2`, …:

```ini
[preheat PLA]
extruder: 210
bed: 60
```

Defining any preheat preset replaces KlipperScreen's default ones (PLA, ABS, PETG,
FLEX).

## Uninstall

```bash
cd ~/medusahc-klipper-screen-add-on
./install.sh -u -r
```

`MedusaHC_screen.conf` is left in place.

## License

AGPLv3, like KlipperScreen.
