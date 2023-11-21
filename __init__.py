import logging

from eventmanager import Evt
from RHUI import UIField, UIFieldType, UIFieldSelectOption

import plugins.VRxC_ELRS.constants as constants
import plugins.VRxC_ELRS.elrsBackpack as elrsBackpack

logger = logging.getLogger(__name__)

PLUGIN_VERSION = 'v1.0.0-beta.3-dev'

def initialize(rhapi):

    logger.info(PLUGIN_VERSION)

    controller = elrsBackpack.elrsBackpack('elrs', 'ELRS', rhapi)

    rhapi.events.on(Evt.VRX_INITIALIZE, controller.registerHandlers)

    #
    # Setup UI
    #
    
    type_hardware = []
    for option in constants.hardwareOptions:
        type_hardware.append(UIFieldSelectOption(label=option.name, value=option.value))

    hardware = UIField('hardware_type', 'ELRS VRx Hardware', field_type = UIFieldType.SELECT, options = type_hardware)
    rhapi.fields.register_pilot_attribute(hardware)

    elrs_bindphrase = UIField(name = 'comm_elrs', label = 'ELRS BP Bindphrase', field_type = UIFieldType.TEXT)
    rhapi.fields.register_pilot_attribute(elrs_bindphrase)

    rhapi.ui.register_panel('elrs_vrxc', 'ExpressLRS VRxC', 'settings', order=0)

    #
    # Check Boxes
    #
    
    _heat_name = UIField('_heat_name', 'Show Heat Name on Stage', field_type = UIFieldType.CHECKBOX)
    rhapi.fields.register_option(_heat_name, 'elrs_vrxc')

    _position_mode = UIField('_position_mode', 'Show Current Position', field_type = UIFieldType.CHECKBOX)
    rhapi.fields.register_option(_position_mode, 'elrs_vrxc')

    _gap_mode = UIField('_gap_mode', 'Show Gap Time', field_type = UIFieldType.CHECKBOX)
    rhapi.fields.register_option(_gap_mode, 'elrs_vrxc')

    _results_mode = UIField('_results_mode', 'Show Post-Race Results', field_type = UIFieldType.CHECKBOX)
    rhapi.fields.register_option(_results_mode, 'elrs_vrxc')

    #
    # Text Fields
    #

    _racestage_message = UIField('_racestage_message', 'Race Stage Message', field_type = UIFieldType.TEXT, value="w ARM NOW x")
    rhapi.fields.register_option(_racestage_message, 'elrs_vrxc')

    _racestart_message = UIField('_racestart_message', 'Race Start Message', field_type = UIFieldType.TEXT, value="w   GO!   x")
    rhapi.fields.register_option(_racestart_message, 'elrs_vrxc')

    _pilotdone_message = UIField('_pilotdone_message', 'Pilot Done Message', field_type = UIFieldType.TEXT, value="w FINISHED! x")
    rhapi.fields.register_option(_pilotdone_message, 'elrs_vrxc')

    _racefinish_message = UIField('_racefinish_message', 'Race Finish Message', field_type = UIFieldType.TEXT, value="w FINISH LAP! x")
    rhapi.fields.register_option(_racefinish_message, 'elrs_vrxc')

    _racestop_message = UIField('_racestop_message', 'Race Stop Message', field_type = UIFieldType.TEXT, value="w  LAND NOW!  x")
    rhapi.fields.register_option(_racestop_message, 'elrs_vrxc')

    _leader_message = UIField('_leader_message', 'Race Leader Message', field_type = UIFieldType.TEXT, value="x RACE LEADER w")
    rhapi.fields.register_option(_leader_message, 'elrs_vrxc')

    #
    # Basic Integers
    #

    _racestart_uptime = UIField('_racestart_uptime', 'Start Message Uptime', desc='decaseconds', field_type = UIFieldType.BASIC_INT, value=5)
    rhapi.fields.register_option(_racestart_uptime, 'elrs_vrxc')

    _finish_uptime = UIField('_finish_uptime', 'Finish Message Uptime', desc='decaseconds', field_type = UIFieldType.BASIC_INT, value=20)
    rhapi.fields.register_option(_finish_uptime, 'elrs_vrxc')

    _results_uptime = UIField('_results_uptime', 'Lap Result Uptime', desc='decaseconds', field_type = UIFieldType.BASIC_INT, value=40)
    rhapi.fields.register_option(_results_uptime, 'elrs_vrxc')

    _announcement_uptime = UIField('_announcement_uptime', 'Announcement Uptime', desc='decaseconds', field_type = UIFieldType.BASIC_INT, value=50)
    rhapi.fields.register_option(_announcement_uptime, 'elrs_vrxc')

    _status_row = UIField('_status_row', 'Race Status Row', desc='Use rows between 0-9 or 15-17', field_type = UIFieldType.BASIC_INT, value=5)
    rhapi.fields.register_option(_status_row, 'elrs_vrxc')

    _currentlap_row = UIField('_currentlap_row', 'Current Lap/Position Row', desc='Use rows between 0-9 or 15-17', field_type = UIFieldType.BASIC_INT, value=0)
    rhapi.fields.register_option(_currentlap_row, 'elrs_vrxc')

    _lapresults_row = UIField('_lapresults_row', 'Lap/Gap Results Row', desc='Use rows between 0-9 or 15-17', field_type = UIFieldType.BASIC_INT, value=15)
    rhapi.fields.register_option(_lapresults_row, 'elrs_vrxc')

    _announcement_row = UIField('_announcement_row', 'Announcement Row', desc='Use rows between 0-9 or 15-17', field_type = UIFieldType.BASIC_INT, value=6)
    rhapi.fields.register_option(_announcement_row, 'elrs_vrxc')

    _bp_repeat = UIField('_bp_repeat', 'Number of times to send message', desc='Field for testing peformance', field_type = UIFieldType.BASIC_INT, value=0)
    rhapi.fields.register_option(_bp_repeat, 'elrs_vrxc')

    _bp_delay = UIField('_bp_delay', 'Send delay between messages', desc='Field for testing peformance in milliseconds', field_type = UIFieldType.BASIC_INT, value=0)
    rhapi.fields.register_option(_bp_delay, 'elrs_vrxc')