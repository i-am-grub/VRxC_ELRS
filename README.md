# RotorHazard VRx Control for the ExpressLRS Backpack

> WARNING: This plugin is still very much in inital developmental state. It currently requires [RotorHazard v4.0.0-beta.5](https://github.com/RotorHazard/RotorHazard/releases/tag/v4.0.0-beta.5) with a few manual modifications until changes are pushed to the main branch

This is a plugin being developed for the RotorHazard timing system. It allows for the ability to send OSD messages to pilots using compatible equipment (such as the HDZero goggles), autmatically switching pilot's video channels and output power (coming soon), and allows for the race manager to start the race from their transmitter (need to see if the transmitter backpack can recieve and process these messages accordingly). 

## Requirements

- RotorHazard v4.0.0-beta.5+ is required to run the plugin

## Installation

1. Use pip to install `esptools`
    - `pip install esptools`
2. To install, clone or copy this repository's folder into RotorHazard's plugin directory `/src/server/plugins`, and (re)start the server.
    - If installation is successful, the RotorHazard log will contain the message `Loaded plugin module VRxC_ELRS` at startup.
3. The plugin should be visable under the Settings tab after rebooting. 

---

## Development notes
1. `'pilot_id' : pilot_obj.id` needs to be manually added to the args passed through the event trigger for `RACE_RACE_LAP_RECORDED` and `RACE_PILOT_DONE`
2. Compile and install the backpack (also being developed in parallel) onto an esp32. The repo and branch and be found [here](https://github.com/i-am-grub/ELRS-Backpack/tree/rotorhazard)
    - No need to add your bind phrase at this stage, but it will be required eventually for starting races from your transmitter
    - There is a Rotorhazard target created within the repo. You will just need to press build and upload for the esp32 to be flashed
    - If you don't want to manually compile, use esptool to flash the firmware in this repo
3. The peers that the backpack is sending messages to are only set after current heat has been changed.
    - After starting up the timer, make sure to first switch to a race you don't want to test before selecting the one you do want to test. This is due to peers only being set on the RACE_SET system event 
        - e.g. If the timer starts with and the current race is set to Race 1, you will need to switch to another race and then reselect Race 1 before the backpack will work
    - Same principles apply if you change a pilot's backpack settings in the format tab and they are in the current race.

## TODO list:
1. Need to write instructions on how to flash the firmware with esptool (temp until the backpack can be merged into the main ELRS Backpack repo)
2. Need to add the ability to bind the backpack to event manager's backpack (for starting races from tranmitter)
3. Figure out why gap_info.next_rank.callsign and gap_info.next_rank.diff_time are not working as expected
4. Complete documentation
5. Auto port discovery with esptool targeting the backpack

## Bugs/Features needed for the plugin to be completed:
1. Default values not being set properly for UIFieldType.SELECT types
2. UIFieldType.SELECT's value being able to be set to an object 
3. Maybe issues with 'gap_info' arg from Evt.RACE_RACE_LAP_RECORDED