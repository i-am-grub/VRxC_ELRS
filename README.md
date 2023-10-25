# RotorHazard VRx Control for the ExpressLRS Backpack

> WARNING: This plugin is still very much in inital developmental state. It is recommended to only install this plugin with the official release of RotorHazard v4.0.0

This is a plugin being developed for the RotorHazard timing system with the following features: 
- Send OSD messages to pilots using compatible equipment (such as the HDZero goggles)
- [In progress] Allows for the race manager to start the race from their transmitter
- [Future Feature] Automatically switching pilot's video channels and output power 

## Requirements

- RotorHazard v4.0.0+ is required to run the plugin

## Installation

### Installing Plugin

1. To install, clone or copy this repository's folder into RotorHazard's plugin directory `/src/server/plugins`, and (re)start the server.
    - If installation is successful, the RotorHazard log will contain the message `Loaded plugin module VRxC_ELRS` at startup.
2. The plugin should be visable under the Settings tab after rebooting. 

### Installing Backpack on ESP32

> NOTE: This process will be replaced by using the ExpressLRS Configurator eventually

> The following chipsets that are **NOT** supported: `ESP8266`, `ESP32S2`, `ESP32S3`, `ESP32H2`, `ESP32C2`, `ESP32C6`

It is currently recommended to use `esptool` to install the backpack firmware. It is not required to complete this step on the RotorHazard server - another computer may be used.

1. Install `python 3.7+` if not already install
2. Install esptool with the following command 

```pip install esptool```

3. Connect your `ESP32` to the computer and find the port it is using. Run the following command
    - Replace `PORT` with the port that the `ESP32` is using
    - Make sure that all of the paths for the `.bin` files are correct. The files are included within this repository

```python -m esptool -p PORT write_flash --erase-all 0x00001000 bootloader.bin 0x00008000 partitions.bin 0x0000e000 boot_app0.bin 0x00010000 firmware.bin```

### Installing Backpack on HDZero Goggles

1. Use the ExpressLRS Configurator to generate the firmware file. It is important to use the following steps to force the overwrite of the default firmware on the goggles.
    1. Open the ExpressLRS Configurator
    2. Select the Backpack tab
    3. Select release `1.3.0` or later
    4. Select the `Goggles` category
    5. Select the `HDZero Goggle VRX Backpack`
    6. Select the `WIFI` Flashing Method
    7. Enter your Binding Phrase. You can **NOT** change this on backpack's configuration page.
    8. Select `Build` (do not use FLASH)
2. Start the Backpack's Wifi (the the goggle's wifi)
3. Connect your computer to the backpack's wifi and open the backpack's configuration page.
    - If you haven't used it before, the webpage is similar to the default ExpressLRS configuration page.
4. Upload the generated file (e.g. `HDZero_Goggle_ESP32_Backpack-1.3.0.bin`) through the configuration page. If it show a warning about overwriting the previous firmware because it has a different name, force the overwrite.

## Settings

### Pilot Settings

![Pilot Settings](docs/pilot_atts.png)

#### ELRS VRx Hardware : SELECTOR

Select the type of hardware that the pilot is using. More options will be added in the future when more devices that support the ExpressLRS Backpack OSD features

#### Use Bindphrase : CHECKBOX

- **TRUE** : Use the `Backpack Bindphrase` field to generate the pilot's UID
- **FALSE** : Use the pilot's callsign to generate the pilot's UID

#### Backpack Bindphrase : TEXT

The pilot's individual bindphrase for their backpack

### General Settings

![General Settings](docs/settings.png)

### Send Message Repeat Count : INT

It is currently noted that transmitters using 2.4 GHz (such as the ExpressLRS protocol) will cause interfence with the packet. Increasing this value will repeat the broadcast of specific messages for redundancy, in case a packet is missed. Increasing this number will slow down the speed at which messages are sent to the pilot's goggles. A setting of 2 or 3 appears to work well for me, but feel free to play around with this setting as needed.

### Show Post Race Results : CHECKBOX

When activated, the pilot will be shown results when they finish the race. It is recommeded to turn off `Post Flight Results` in Betaflight so the results won't be overridden when the pilot lands.

### Practice Mode : CHECKBOX

Instead of shown the gap time between pilots and in a race, the pilots will be shown lap times instead.