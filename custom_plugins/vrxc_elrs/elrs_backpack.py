import logging
import hashlib
import serial

import serial.serialutil
import serial.tools.list_ports
import gevent
import gevent.lock
import gevent.queue

import RHUtils
from RHRace import RaceStatus, WinCondition
from VRxControl import VRxController

from .msp import msptypes, msp_message

logger = logging.getLogger(__name__)


class ELRSBackpack(VRxController):

    _queue_lock = gevent.lock.RLock()
    _backpack_connected = False
    _backpack_queue = None

    def __init__(self, name, label, rhapi):
        super().__init__(name, label)
        self._rhapi = rhapi

    def registerHandlers(self, args):
        args["register_fn"](self)

    def start_race(self):
        if self._rhapi.db.option("_race_start") == "1":
            start_race_args = {"start_time_s": 10}
            if self._rhapi.race.status == RaceStatus.READY:
                self._rhapi.race.stage(start_race_args)

    def stop_race(self):
        if self._rhapi.db.option("_race_stop") == "1":
            status = self._rhapi.race.status
            if status == RaceStatus.STAGING or status == RaceStatus.RACING:
                self._rhapi.race.stop()

    #
    # Backpack communications
    #

    def combine_bytes(self, a, b):
        return (b << 8) | a

    def connection_search(self, args={"from_ui": False}):

        if self._backpack_connected and args["from_ui"]:
            message = "Backpack already connected."
            self._rhapi.ui.message_notify(self._rhapi.language.__(message))
            return
        elif args["from_ui"]:
            message = "Starting scan for backpack"
            self._rhapi.ui.message_notify(self._rhapi.language.__(message))

        version = msp_message()
        version.set_function(msptypes.MSP_ELRS_GET_BACKPACK_VERSION)
        version_message = version.get_msp()

        logger.info("Attempting to find backpack")

        AVOIDED_PORTS = ["/dev/ttyAMA0", "/dev/ttyAMA10", "COM1"]
        ports = list(serial.tools.list_ports.comports())
        to_remove = []

        for port in ports:
            if port.device in AVOIDED_PORTS:
                to_remove.append(port)

        for port in to_remove:
            ports.remove(port)

        for port in ports:
            try:
                connection = serial.Serial(
                    port=port.device,
                    baudrate=460800,
                    bytesize=8,
                    parity="N",
                    stopbits=1,
                    timeout=0.1,
                    xonxoff=0,
                    rtscts=0,
                    write_timeout=0.1,
                )
            except:
                logger.warning(
                    "Failed to open serial device. Attempting to connect to new device..."
                )
                continue

            # Some devkits need extra time to establish the connection
            gevent.sleep(2)

            # Clear out any previous data in the serial buffer
            connection.read_all()

            try:
                connection.write(version_message)
            except:
                logger.error(
                    "Failed to write to open serial device. Attempting to connect to new device..."
                )
                connection.close()
                continue

            response = list(connection.read(8))
            if len(response) == 8:
                logger.info(f"Device response: {response}")
                if response[:3] == [ord("$"), ord("X"), ord(">")]:
                    mode = self.combine_bytes(response[4], response[5])
                    payload_length = self.combine_bytes(response[6], response[7])
                    payload = list(connection.read(payload_length))
                    checksum = list(connection.read(1))

                    if mode == msptypes.MSP_ELRS_GET_BACKPACK_VERSION:
                        version_list = [chr(val) for val in payload]
                        backpack_version = "".join(version_list)
                        message = f"Connected to backpack on {port.device} with firmware version {backpack_version}"

                        if args["from_ui"]:
                            self._rhapi.ui.message_notify(
                                self._rhapi.language.__(message)
                            )
                        logger.info(message)

                        self._backpack_connected = True
                        self._backpack_queue = gevent.queue.Queue()
                        gevent.spawn(self.backpack_loop, connection)
                        return

                    else:
                        logger.warning(
                            f"Unexpected response from {port.device}, trying next port..."
                        )
                        connection.close()
                        continue
                else:
                    logger.warning(
                        f"Unrecongnized response from {port.device}, trying next port..."
                    )
                    connection.close()
                    continue
            else:
                logger.warning(f"Bad response from {port.device}, trying next port...")
                connection.close()
                continue
        else:
            message = "Could not find connected backpack."
            logger.warning(message)

            if args["from_ui"]:
                self._rhapi.ui.message_notify(self._rhapi.language.__(message))

    def backpack_loop(self, connection: serial.Serial):

        try:
            while True:

                # Writing to backpack
                while not self._backpack_queue.empty():
                    # A slight delay is needed between writing messages
                    # to the connected backpack
                    gevent.sleep(0.001)
                    message = self._backpack_queue.get()
                    connection.write(message)

                # Reading from backpack
                packet = list(connection.read(8))
                if len(packet) == 8:
                    if packet[:3] == [ord("$"), ord("X"), ord("<")]:
                        mode = self.combine_bytes(packet[4], packet[5])
                        payload_length = self.combine_bytes(packet[6], packet[7])
                        payload = list(connection.read(payload_length))
                        checksum = list(connection.read(1))

                        # Monitor SET_RECORDING_STATE for controlling race
                        if mode == msptypes.MSP_ELRS_BACKPACK_SET_RECORDING_STATE:
                            if payload[0] == 0x00:
                                gevent.spawn(self.stop_race)
                            elif payload[0] == 0x01:
                                gevent.spawn(self.start_race)

                gevent.sleep(0.01)

        except KeyboardInterrupt:
            logger.error("Stopping blackpack connector greenlet")

        except serial.serialutil.SerialException:
            error_message = "Failed to properly communicate with the ELRS backpack. Disabling the backpack connection; rescan to re-enable."
            logger.error(error_message)
            self._rhapi.ui.message_alert(self._rhapi.language.__(error_message))

        finally:
            connection.close()
            self._backpack_connected = False

    #
    # Backpack message generation
    #

    def hash_phrase(self, bindphrase: str) -> list:
        bindingPhraseHash = [
            x
            for x in hashlib.md5(
                (f'-DMY_BINDING_PHRASE="{bindphrase}"').encode()
            ).digest()[0:6]
        ]
        if (bindingPhraseHash[0] % 2) == 1:
            bindingPhraseHash[0] -= 0x01
        return bindingPhraseHash

    def get_pilot_UID(self, pilot_id):
        bindphrase = self._rhapi.db.pilot_attribute_value(pilot_id, "comm_elrs")
        if bindphrase:
            uid = self.hash_phrase(bindphrase)
        else:
            uid = self.hash_phrase(self._rhapi.db.pilot_by_id(pilot_id).callsign)

        return uid

    def centerOSD(self, stringlength):
        offset = int(stringlength / 2)
        col = int(50 / 2) - offset
        if col < 0:
            col = 0
        return col

    def queue_add(self, msp):
        if self._backpack_connected:
            self._backpack_queue.put(msp)

    def send_msp(self, msp):
        self.queue_add(msp)

    def set_sendUID(self, bindingHash: list):
        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_SET_SEND_UID)
        message.set_payload([1] + bindingHash)
        self.send_msp(message.get_msp())

    def clear_sendUID(self):
        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_SET_SEND_UID)
        message.set_payload([0])
        self.send_msp(message.get_msp())

    def send_clear(self, attempts=1):
        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_SET_OSD)
        message.set_payload([0x02])

        for attempt in range(attempts):
            self.send_msp(message.get_msp())

    def send_msg(self, row, col, str):
        payload = [0x03, row, col, 0]
        for x in [*str]:
            payload.append(ord(x))

        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_SET_OSD)
        message.set_payload(payload)
        self.send_msp(message.get_msp())

    def send_display(self, attempts=1):
        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_SET_OSD)
        message.set_payload([0x04])

        for attempt in range(attempts):
            self.send_msp(message.get_msp())

    def send_clear_row(self, row, attempts=1):
        payload = [0x03, row, 0, 0]
        for x in range(50):
            payload.append(0)

        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_SET_OSD)
        message.set_payload(payload)

        for attempt in range(attempts):
            self.send_msp(message.get_msp())

    def activate_bind(self, _args):
        message = "Activating backpack's bind mode..."
        self._rhapi.ui.message_notify(self._rhapi.language.__(message))
        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_BACKPACK_SET_MODE)
        message.set_payload([ord("B")])
        self.send_msp(message.get_msp())

    def activate_wifi(self, _args):
        message = "Turning on backpack's wifi..."
        self._rhapi.ui.message_notify(self._rhapi.language.__(message))
        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_BACKPACK_SET_MODE)
        message.set_payload([ord("W")])
        self.send_msp(message.get_msp())

    #
    # Connection Test
    #

    def test_osd(self, _args):

        def test():
            self._queue_lock.acquire()
            message = "ROTORHAZARD"
            for row in range(18):

                self.send_clear()
                start_col = self.centerOSD(len(message))
                self.send_msg(row, start_col, message)
                self.send_display()

                gevent.sleep(0.5)

                self.send_clear_row(row)
                self.send_display()

            gevent.sleep(1)
            self.send_clear()
            self.send_display()
            self._queue_lock.release()

        gevent.spawn(test)

    #
    # VRxC Event Triggers
    #

    def onStartup(self, _args):
        gevent.spawn(self.connection_search, {"from_ui": False})

    def onPilotAlter(self, args):
        pilot_id = args["pilot_id"]
        uid = self.get_pilot_UID(pilot_id)
        logger.info(f"Pilot {pilot_id}'s UID set to {uid}")

    def onRaceStage(self, args):

        if not self._backpack_connected:
            return

        use_heat_name = self._rhapi.db.option("_heat_name") == "1"
        use_round_num = self._rhapi.db.option("_round_num") == "1"
        use_class_name = self._rhapi.db.option("_class_name") == "1"
        use_event_name = self._rhapi.db.option("_event_name") == "1"

        # Pull heat name and rounds
        heat_data = self._rhapi.db.heat_by_id(args["heat_id"])
        if heat_data:
            class_id = heat_data.class_id
            heat_name = heat_data.display_name
            round_num = self._rhapi.db.heat_max_round(args["heat_id"]) + 1
        else:
            class_id = None
            heat_name = None
            round_num = None

        # Check class name
        if class_id:
            raceclass = self._rhapi.db.raceclass_by_id(class_id)
            class_name = raceclass.display_name
        else:
            raceclass = None
            class_name = None

        # Generate heat message
        heat_name_row = self._rhapi.db.option("_heatname_row")
        if all([use_heat_name, use_round_num, heat_name, round_num]):
            round_trans = self._rhapi.__("Round")
            heat_message = (
                f"x {heat_name.upper()} | {round_trans.upper()} {round_num} w"
            )
            heat_start_col = self.centerOSD(len(heat_message))
            heat_message_parms = (heat_name_row, heat_start_col, heat_message)
        elif use_heat_name and heat_name:
            heat_message = f"x {heat_name.upper()} w"
            heat_start_col = self.centerOSD(len(heat_message))
            heat_message_parms = (heat_name_row, heat_start_col, heat_message)

        # Generate class message
        class_name_row = self._rhapi.db.option("_classname_row")
        if use_class_name and class_name:
            class_message = f"x {class_name.upper()} w"
            class_start_col = self.centerOSD(len(class_message))
            class_message_parms = (class_name_row, class_start_col, class_message)

        # Generate event message
        event_name_row = self._rhapi.db.option("_eventname_row")
        event_name = self._rhapi.db.option("eventName")
        if use_event_name and event_name:
            event_name = self._rhapi.db.option("eventName")
            event_message = heat_message = f"x {event_name.upper()} w"
            event_start_col = self.centerOSD(len(heat_message))
            event_message_parms = (event_name_row, event_start_col, event_message)

        start_col = self.centerOSD(len(self._rhapi.db.option("_racestage_message")))
        stage_mesage = (
            self._rhapi.db.option("_status_row"),
            start_col,
            self._rhapi.db.option("_racestage_message"),
        )

        # Send stage message to all pilots
        def arm(pilot_id):
            uid = self.get_pilot_UID(pilot_id)
            self._queue_lock.acquire()
            self.set_sendUID(uid)
            self.send_clear()

            # Send messages to backpack
            self.send_msg(*stage_mesage)
            if use_heat_name and heat_name:
                self.send_msg(*heat_message_parms)
            if use_class_name and class_name:
                self.send_msg(*class_message_parms)
            if use_event_name and event_name:
                self.send_msg(*event_message_parms)

            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

        seat_pilots = self._rhapi.race.pilots
        for seat in seat_pilots:
            if (
                seat_pilots[seat]
                and self._rhapi.db.pilot_attribute_value(
                    seat_pilots[seat], "elrs_active"
                )
                == "1"
            ):
                gevent.spawn(arm, seat_pilots[seat])

    def onRaceStart(self, _args):

        if not self._backpack_connected:
            return

        def start(pilot_id):
            uid = self.get_pilot_UID(pilot_id)
            start_col = self.centerOSD(len(self._rhapi.db.option("_racestart_message")))

            self._queue_lock.acquire()
            self.set_sendUID(uid)

            self.send_clear(2)

            self.send_msg(
                self._rhapi.db.option("_status_row"),
                start_col,
                self._rhapi.db.option("_racestart_message"),
            )
            self.send_display(2)
            self.clear_sendUID()
            self._queue_lock.release()

            gevent.sleep(self._rhapi.db.option("_racestart_uptime") * 1e-1)

            self._queue_lock.acquire()
            self.set_sendUID(uid)
            self.send_clear_row(self._rhapi.db.option("_status_row"), 2)
            self.send_display(2)
            self.clear_sendUID()
            self._queue_lock.release()

        seat_pilots = self._rhapi.race.pilots
        for seat in seat_pilots:
            if (
                seat_pilots[seat]
                and self._rhapi.db.pilot_attribute_value(
                    seat_pilots[seat], "elrs_active"
                )
                == "1"
            ):
                gevent.spawn(start, seat_pilots[seat])

    def onRaceFinish(self, _args):

        if not self._backpack_connected:
            return

        def finish(pilot_id):
            uid = self.get_pilot_UID(pilot_id)
            start_col = self.centerOSD(
                len(self._rhapi.db.option("_racefinish_message"))
            )

            self._queue_lock.acquire()
            self.set_sendUID(uid)
            self.send_clear_row(self._rhapi.db.option("_status_row"))
            self.send_msg(
                self._rhapi.db.option("_status_row"),
                start_col,
                self._rhapi.db.option("_racefinish_message"),
            )
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

            gevent.sleep(self._rhapi.db.option("_finish_uptime") * 1e-1)

            self._queue_lock.acquire()
            self.set_sendUID(uid)
            self.send_clear_row(self._rhapi.db.option("_status_row"))
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

        seat_pilots = self._rhapi.race.pilots
        seats_finished = self._rhapi.race.seats_finished

        for seat in seat_pilots:
            if (
                seat_pilots[seat]
                and self._rhapi.db.pilot_attribute_value(
                    seat_pilots[seat], "elrs_active"
                )
                == "1"
            ):
                if not seats_finished[seat]:
                    gevent.spawn(finish, seat_pilots[seat])

    def onRaceStop(self, _args):

        if not self._backpack_connected:
            return

        def land(pilot_id):
            uid = self.get_pilot_UID(pilot_id)
            start_col = self.centerOSD(len(self._rhapi.db.option("_racestop_message")))

            self._queue_lock.acquire()
            self.set_sendUID(uid)
            self.send_msg(
                self._rhapi.db.option("_status_row"),
                start_col,
                self._rhapi.db.option("_racestop_message"),
            )
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

        seat_pilots = self._rhapi.race.pilots
        seats_finished = self._rhapi.race.seats_finished

        for seat in seat_pilots:
            if (
                seat_pilots[seat]
                and self._rhapi.db.pilot_attribute_value(
                    seat_pilots[seat], "elrs_active"
                )
                == "1"
            ):
                if not seats_finished[seat]:
                    gevent.spawn(land, seat_pilots[seat])

    def onRaceLapRecorded(self, args):

        if not self._backpack_connected:
            return

        def update_pos(result):
            pilot_id = result["pilot_id"]

            if self._rhapi.db.option("_position_mode") != "1":
                message = f"LAP: {result['laps'] + 1}"
            else:
                message = f"POSN: {str(result['position']).upper()} | LAP: {result['laps'] + 1}"
            start_col = self.centerOSD(len(message))

            uid = self.get_pilot_UID(pilot_id)
            self._queue_lock.acquire()
            self.set_sendUID(uid)
            self.send_clear_row(self._rhapi.db.option("_currentlap_row"))

            self.send_msg(self._rhapi.db.option("_currentlap_row"), start_col, message)
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

        def lap_results(result, gap_info):
            pilot_id = result["pilot_id"]

            if self._rhapi.db.option("_gap_mode") != "1":
                if gap_info.race.win_condition == WinCondition.FASTEST_CONSECUTIVE:
                    formatted_time1 = RHUtils.time_format(
                        gap_info.current.last_lap_time, "{m}:{s}.{d}"
                    )
                    formatted_time2 = RHUtils.time_format(
                        gap_info.current.consecutives, "{m}:{s}.{d}"
                    )
                    message = f"x {formatted_time1} | {gap_info.current.consecutives_base}/{formatted_time2} w"
                elif (
                    gap_info.race.win_condition == WinCondition.FASTEST_LAP
                    and gap_info.current.is_best
                ):
                    formatted_time = RHUtils.time_format(
                        gap_info.current.last_lap_time, "{m}:{s}.{d}"
                    )
                    message = f"x BEST LAP | {formatted_time} w"
                else:
                    formatted_time1 = RHUtils.time_format(
                        gap_info.current.last_lap_time, "{m}:{s}.{d}"
                    )
                    formatted_time2 = RHUtils.time_format(
                        gap_info.current.total_time_laps, "{m}:{s}.{d}"
                    )
                    message = f"x {formatted_time1} | {formatted_time2} w"

            elif gap_info.race.win_condition == WinCondition.FASTEST_CONSECUTIVE:
                formatted_time1 = RHUtils.time_format(
                    gap_info.current.last_lap_time, "{m}:{s}.{d}"
                )
                formatted_time2 = RHUtils.time_format(
                    gap_info.current.consecutives, "{m}:{s}.{d}"
                )
                message = f"x {formatted_time1} | {gap_info.current.consecutives_base}/{formatted_time2} w"

            elif gap_info.race.win_condition == WinCondition.FASTEST_LAP:
                if gap_info.next_rank.diff_time:
                    formatted_time = RHUtils.time_format(
                        gap_info.next_rank.diff_time, "{m}:{s}.{d}"
                    )
                    formatted_callsign = str.upper(gap_info.next_rank.callsign)
                    message = f"x {formatted_callsign} | +{formatted_time} w"

                elif gap_info.current.is_best_lap and gap_info.current.lap_number:
                    formatted_time = RHUtils.time_format(
                        gap_info.current.last_lap_time, "{m}:{s}.{d}"
                    )
                    message = f"x {self._rhapi.db.option('_leader_message')} | {formatted_time} w"

                elif gap_info.current.lap_number:
                    formatted_time = RHUtils.time_format(
                        gap_info.first_rank.diff_time, "{m}:{s}.{d}"
                    )
                    formatted_callsign = str.upper(gap_info.first_rank.callsign)
                    message = f"x {formatted_callsign} | +{formatted_time} w"

            else:
                if gap_info.next_rank.diff_time:
                    formatted_time = RHUtils.time_format(
                        gap_info.next_rank.diff_time, "{m}:{s}.{d}"
                    )
                    formatted_callsign = str.upper(gap_info.next_rank.callsign)
                    message = f"x {formatted_callsign} | +{formatted_time} w"

                elif gap_info.current.lap_number:
                    formatted_time = RHUtils.time_format(
                        gap_info.current.last_lap_time, "{m}:{s}.{d}"
                    )
                    message = f"x {self._rhapi.db.option('_leader_message')} | {formatted_time} w"

            start_col = self.centerOSD(len(message))

            uid = self.get_pilot_UID(pilot_id)
            self._queue_lock.acquire()
            self.set_sendUID(uid)
            self.send_msg(self._rhapi.db.option("_lapresults_row"), start_col, message)
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

            gevent.sleep(self._rhapi.db.option("_results_uptime") * 1e-1)

            self._queue_lock.acquire()
            self.set_sendUID(uid)
            self.send_clear_row(self._rhapi.db.option("_lapresults_row"))
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

        seats_finished = self._rhapi.race.seats_finished
        pilots_completion = {}
        for slot, pilot_id in self._rhapi.race.pilots.items():
            if pilot_id:
                pilots_completion[pilot_id] = seats_finished[slot]

        results = args["results"]["by_race_time"]
        for result in results:

            if (
                self._rhapi.db.pilot_attribute_value(result["pilot_id"], "elrs_active")
                == "1"
            ):

                if not pilots_completion[result["pilot_id"]]:
                    gevent.spawn(update_pos, result)

                    if result["pilot_id"] == args["pilot_id"] and (result["laps"] > 0):
                        gevent.spawn(lap_results, result, args["gap_info"])

    def onLapDelete(self, _args):

        if not self._backpack_connected:
            return

        def delete(pilot_id):
            uid = self.get_pilot_UID(pilot_id)
            self._queue_lock.acquire()
            self.set_sendUID(uid)
            self.send_clear()
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

        if self._rhapi.db.option("_results_mode") == "1":
            seat_pilots = self._rhapi.race.pilots
            for seat in seat_pilots:
                if (
                    seat_pilots[seat]
                    and self._rhapi.db.pilot_attribute_value(
                        seat_pilots[seat], "elrs_active"
                    )
                    == "1"
                ):
                    gevent.spawn(delete, seat_pilots[seat])

    def onRacePilotDone(self, args):

        if not self._backpack_connected:
            return

        def done(result, win_condition):

            pilot_id = result["pilot_id"]
            start_col = self.centerOSD(len(self._rhapi.db.option("_pilotdone_message")))
            results_row1 = self._rhapi.db.option("_results_row")
            results_row2 = results_row1 + 1

            uid = self.get_pilot_UID(pilot_id)
            self._queue_lock.acquire()
            self.set_sendUID(uid)
            self.send_clear_row(self._rhapi.db.option("_currentlap_row"))
            self.send_clear_row(self._rhapi.db.option("_status_row"))
            self.send_msg(
                self._rhapi.db.option("_status_row"),
                start_col,
                self._rhapi.db.option("_pilotdone_message"),
            )

            if self._rhapi.db.option("_results_mode") == "1":
                placement_message = f'PLACEMENT: {result["position"]}'
                place_col = self.centerOSD(len(placement_message))
                self.send_msg(results_row1, place_col, placement_message)

                if win_condition == WinCondition.FASTEST_CONSECUTIVE:
                    win_message = f'FASTEST {result["consecutives_base"]} CONSEC: {result["consecutives"]}'
                elif win_condition == WinCondition.FASTEST_LAP:
                    win_message = f'FASTEST LAP: {result["fastest_lap"]}'
                elif win_condition == WinCondition.FIRST_TO_LAP_X:
                    win_message = f'TOTAL TIME: {result["total_time"]}'
                else:
                    win_message = f'LAPS COMPLETED: {result["laps"]}'

                win_col = self.centerOSD(len(win_message))
                self.send_msg(results_row2, win_col, win_message)

            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

            gevent.sleep(self._rhapi.db.option("_finish_uptime") * 1e-1)

            self._queue_lock.acquire()
            self.set_sendUID(uid)
            self.send_clear_row(self._rhapi.db.option("_status_row"))
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

        results = args["results"]
        leaderboard = results[results["meta"]["primary_leaderboard"]]
        for result in leaderboard:
            if (
                self._rhapi.db.pilot_attribute_value(args["pilot_id"], "elrs_active")
                == "1"
            ) and (result["pilot_id"] == args["pilot_id"]):
                gevent.spawn(done, result, results["meta"]["win_condition"])
                break

    def onLapsClear(self, _args):

        if not self._backpack_connected:
            return

        def clear(pilot_id):
            uid = self.get_pilot_UID(pilot_id)
            self._queue_lock.acquire()
            self.set_sendUID(uid)
            self.send_clear()
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

        seat_pilots = self._rhapi.race.pilots
        for seat in seat_pilots:
            if (
                seat_pilots[seat]
                and self._rhapi.db.pilot_attribute_value(
                    seat_pilots[seat], "elrs_active"
                )
                == "1"
            ):
                gevent.spawn(clear, seat_pilots[seat])

    def onSendMessage(self, args):

        if not self._backpack_connected:
            return

        def notify(pilot):
            uid = self.get_pilot_UID(pilot)
            start_col = self.centerOSD(len(args["message"]))
            self._queue_lock.acquire()
            self.set_sendUID(uid)
            self.send_msg(
                self._rhapi.db.option("_announcement_row"),
                start_col,
                f"x {str.upper(args['message'])} w",
            )
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

            gevent.sleep(self._rhapi.db.option("_announcement_uptime") * 1e-1)

            self._queue_lock.acquire()
            self.set_sendUID(uid)
            self.send_clear_row(self._rhapi.db.option("_announcement_row"))
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

        seat_pilots = self._rhapi.race.pilots
        for seat in seat_pilots:
            if (
                seat_pilots[seat]
                and self._rhapi.db.pilot_attribute_value(
                    seat_pilots[seat], "elrs_active"
                )
                == "1"
            ):
                gevent.spawn(notify, seat_pilots[seat])
