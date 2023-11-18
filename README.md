# RotorHazard VRx Control for the ExpressLRS Backpack

> WARNING: This plugin is still very much in inital developmental state. It is recommended to only install this plugin with the official release of RotorHazard v4.0.0

This is a plugin being developed for the RotorHazard timing system with the following features: 
- [X] Send OSD messages to pilots using compatible equipment (such as the HDZero goggles)
- [X] Allows for the race manager to start the race from their transmitter
- [ ] Automatically switching pilot's video channels and output power 

## Requirements

- RotorHazard v4.0.0+ is required to run the plugin

## Installation

### Installing RH Plugin and Backpack on ESP32

To install, follow the instructions on the [latest release](https://github.com/i-am-grub/VRxC_ELRS/releases) of the plugin.

### Installing Backpack on HDZero Goggles

1. Use the ExpressLRS Configurator to generate the firmware file. It is important to use the following steps to force the overwrite of the default firmware on the goggles.
    1. Open the ExpressLRS Configurator
    2. Select the Backpack tab
    3. Select release `1.3.0` (firmware provided by the ELRS Configurator later than `v1.3.0` doesn't appear to be working)
    4. Select the `Goggles` category
    5. Select the `HDZero Goggle VRX Backpack`
    6. Select the `WIFI` Flashing Method
    7. Enter your Binding Phrase. You can **NOT** change this on backpack's configuration page.
    8. Select `Build` (do not use FLASH)
2. Start the Backpack's Wifi (the the goggle's wifi)
3. Connect your computer to the backpack's wifi and open the backpack's configuration page.
    - If you haven't used it before, the webpage is similar to the default ExpressLRS configuration page.
4. Upload the generated file (e.g. `HDZero_Goggle_ESP32_Backpack-1.3.0.bin`) through the configuration page. If it show a warning about overwriting the previous firmware because it has a different name, force the overwrite.

## Control the Race from the Race Director's Transmitter

There is a feature to control the race from the Race Director's transmitter by tracking the position of the `DVR Rec` switch setup within the ransmitter's backpack. Currently only starting and stopping the race are supported.

> Note: This feature requires the Race Director to have the ELRS Backpack setup on their transmitter. Please ensure this is setup before completing the following instructions.

1. Setup the `DVR Rec` switch in the ELRS backpack
    1. Open the ExpressLRS Lua script (v3 is recommended) on the transmitter
    2. Open up the Backpack settings
    3. Set the AUX channel for `DVR Rec`
    > WARNING: It is not recommended to use the same AUX channel as your ARM switch. 
    > Note: This will not not stop the ability to start recording DVR through this switch. It is just a state that the race timer packpack listens for.
2. Bind the Race Timer backpack to the Transmitter
    1. Start the RotorHazard server with the ESP32 connected.
    2. Navigate to the `ExpressLRS VRxC` settings panel.
    3. Click the `Start Backpack Bind` button.
    4. Within the ExpressLRS Lua script on the transmitter, click `Bind`

To test to see if the backpack was bound sucessfully, naviagte the the `Race` tab within RotorHazard, and use the `DVR Rec` switch to start the race.

> Note: Anytime the backpack needs to be bound to a new transmitter, it will be easiest to reflash the ESP32 with the firmware in the latest release, and then rebind. Rebinding after the device is already bound doesn't appear to work as expected.

## Settings

### Pilot Settings

![Pilot Settings](docs/pilot_atts.png)

#### ELRS VRx Hardware : SELECTOR

Select the type of hardware that the pilot is using. More options will be added in the future when more devices that support the ExpressLRS Backpack OSD features

#### Backpack Bindphrase : TEXT

The pilot's individual bindphrase for their backpack

### General Settings

![General Settings](docs/settings.png)

#### Show Current Position : CHECKBOX

NOT ACTIVATED: Only shows current lap
ACTIVATED: Shows current position and current lap when multiple pilots are in a race

#### Show Gap Time : CHECKBOX

NOT ACTIVATED: Shows lap result time
ACTIVATED: Shows the gap time to next pilot

#### Show Post-Race Results : CHECKBOX

ACTIVATED: The pilot will be shown results when they finish the race. It is recommeded to turn off `Post Flight Results` in Betaflight so the results won't be overridden when the pilot lands.

Please Note:
- HDZero OSD Rows 10-14 are used by this feature

#### Race Stage Message : TEXT

The message shown to pilots when the timer is staging the race

#### Race Start Message : TEXT

The message shown to pilots when the race first starts

#### Pilot Done Message : TEXT

The message shown to pilots when the pilot finishes

#### Race Finish Message : TEXT

The message shown to pilots when the time runs runs out

#### Race Stop Message : TEXT

The message shown to pilots when the the race is stopped

#### Race Leader Message : TEXT

The message shown to pilots when `Show Gap Time` is enabled and the pilot is leading the race

#### Start Message Uptime (decaseconds) : INT

The length of time `Race Start Message` is shown to pilots

#### Finish Message Uptime (decaseconds) : INT

The length of time `Pilot Done Message` and `Race Finish Message` is shown to pilots

#### Lap Result Uptime (decaseconds) : INT

Length of time the pilot's lap or gap time is shown after completing a lap. 

#### Announcement Uptime (decaseconds) : INT

Length of time to show announcements to pilots. (e.g. When a race is scheduled)

#### Race Status Row (0-9 or 15-17) : INT

> Note: Rows 10-14 are used by `Show Post-Race Results` when it is enabled. You can use those rows if the feature is disabled.

Row to show race status messages.

#### Current Lap/Position Row (0-9 or 15-17) : INT

> Note: Rows 10-14 are used by `Show Post-Race Results` when it is enabled. You can use those rows if the feature is disabled.

Row to show current lap and position

#### Lap/Gap Results Row (0-9 or 15-17) : INT

> Note: Rows 10-14 are used by `Show Post-Race Results` when it is enabled. You can use those rows if the feature is disabled.

Row to show lap or gap time

#### Announcement Row (0-9 or 15-17) : INT

> Note: Rows 10-14 are used by `Show Post-Race Results` when it is enabled. You can use those rows if the feature is disabled.

Row to show announcements

#### CALIBRATION: Number of times to send message : INT

> It is advised that the Race Director should try to find the values that work best for their group. Inceasing the number may help with dropped packets, but will decrease ideal peformance - this setting should be tuned to be as low as possible.

A setting to help with dropped packets. This setting determines the number of times a message should be repeated every time it is sent.

#### CALIBRATION: Send delay between messages (milliseconds) : INT

> It is advised that the Race Director should try to find the values that work best for their group. Inceasing the number may help with dropped packets, but will decrease ideal peformance - this setting should be tuned to be as low as possible.

A setting to help with dropped packets. This setting determines the speed at which the backpack sends messages.