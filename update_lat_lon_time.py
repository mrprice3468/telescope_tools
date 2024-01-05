from serial import Serial
from serial.tools.list_ports import comports
from pynmeagps import NMEAReader
from dataclasses import dataclass
from nexstar import NexstarHandController, NexstarModel
from typing import Optional
from timezonefinder import TimezoneFinder

import datetime
import pytz
import re


@dataclass
class PortIDs:
    gps: str
    telescope: str


def identify_ports() -> PortIDs:
    GPS_ID = ("1546", "01A7")
    NEXSTAR_ID = ("067B", "23D3")

    ports_by_id = {}
    ports = comports()
    for port, desc, hwid in sorted(ports):
        re_match = re.search(
            "VID:PID=([0-9A-F]{4}):([0-9A-F]{4})\\b", hwid, re.IGNORECASE
        )
        if not re_match:
            print(f"Failed to match USB VendorID: {hwid=}")
            continue
        vendor_id = re_match.group(1)
        product_id = re_match.group(2)
        print(f"{port}: {vendor_id=}, {product_id=}")
        ports_by_id[(vendor_id, product_id)] = port

    if GPS_ID not in ports_by_id:
        raise Exception('Could not find the USB GPS device.')

    if NEXSTAR_ID not in ports_by_id:
        raise Exception('Could not find the USB NexStar telescope device.')

    return PortIDs(gps=ports_by_id[GPS_ID], telescope=ports_by_id[NEXSTAR_ID])


@dataclass
class Fix:
    lat: float
    lon: float
    date: datetime.date
    time: datetime.time


"""
DOP value	Rating[5]	Description
<1	Ideal	Highest possible confidence level to be used for applications demanding the highest possible precision at all times.
1-2	Excellent	At this confidence level, positional measurements are considered accurate enough to meet all but the most sensitive applications.
2-5	Good	Represents a level that marks the minimum appropriate for making accurate decisions. Positional measurements could be used to make reliable in-route navigation suggestions to the user.
5-10	Moderate	Positional measurements could be used for calculations, but the fix quality could still be improved. A more open view of the sky is recommended.
10-20	Fair	Represents a low confidence level. Positional measurements should be discarded or used only to indicate a very rough estimate of the current location.
>20	Poor	At this level, measurements should be discarded.
"""


def wait_for_fix(nmr: NMEAReader, hdop: int = 3) -> Optional[Fix]:
    lat = None
    lon = None
    date = None
    time = None
    while True:
        try:
            (raw_data, msg) = nmr.read()
            if msg.msgID == "GGA":
                print(
                    "GPS",
                    "GGA",
                    msg.lat,
                    msg.NS,
                    msg.lon,
                    msg.EW,
                    msg.alt,
                    msg.time,
                    msg.numSV,
                    msg.quality,
                    msg.HDOP,
                )
                if msg.HDOP < 3:
                    lat = msg.lat
                    lon = msg.lon
                    time = msg.time
                else:
                    print("Degredation of Precision is too high.")
            if msg.msgID == "RMC":
                print("GPS", "RMC", msg.status, msg.date, msg.time)
                if msg.status == "A":
                    date = msg.date
                else:
                    print("Data frame is not valid.")
            if all(val is not None for val in [lat, lon, date, time]):
                return Fix(lat=lat, lon=lon, date=date, time=time)

        except KeyboardInterrupt:
            return None


def set_telescope_from_gps(ports: PortIDs):
    tf = TimezoneFinder()
    tz_utc = pytz.timezone("UTC")

    with Serial(ports.gps) as nmea_serial, Serial(ports.telescope) as nexstar_serial:
        # Create a controller and try to ping the telescope.
        controller = NexstarHandController(nexstar_serial)
        try:
            print("NexStar Model: ", NexstarModel(controller.getModel()))
            v = controller.getVersion()
            print(f"NexStar Version: {v[0]}.{v[1]}")

        except Exception as ex:
            print("Exception occurred:", ex)
            exit

        nmea_serial.reset_input_buffer()
        nmr = NMEAReader(nmea_serial)

        fix = wait_for_fix(nmr)
        if fix is None:
            print("Failed to acquire a fix. :(")

        # Figure out our current timezone and UTC offset.
        tz_local_str = tf.timezone_at(lat=fix.lat, lng=fix.lon)
        tz_local = pytz.timezone(tz_local_str)

        dt_utc = datetime.datetime(
            year=fix.date.year,
            month=fix.date.month,
            day=fix.date.day,
            hour=fix.time.hour,
            minute=fix.time.minute,
            second=fix.time.second,
            tzinfo=tz_utc,
        )

        dt_local = dt_utc.astimezone(tz_local)
        is_daylight_saving_time = int(dt_local.dst().total_seconds() > 0)

        controller.setTime(timestamp=dt_local, dst=is_daylight_saving_time)
        controller.setLocation(fix.lat, fix.lon)

        print("UTC:  ", dt_utc)
        print("Local:", dt_local)
        print("DST:  ", True if is_daylight_saving_time else False)
        print(f"GPS:  {fix.lat},{fix.lon}")

        print("Set telescope location:", controller.getLocation())
        print("Set telescope time:", controller.getTime())
        print("Current telescope position:", controller.getPosition())


if __name__ == "__main__":
    ports = identify_ports()
    print(ports)
    set_telescope_from_gps(ports)
