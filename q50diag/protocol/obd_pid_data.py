# SPDX-License-Identifier: CC-BY-SA-4.0
# SPDX-FileCopyrightText: OBDb SAEJ1979 contributors (https://github.com/OBDb/SAEJ1979)
"""SAE J1979 field positions and scales, adapted from the OBDb SAEJ1979 signal set.

Source: https://github.com/OBDb/SAEJ1979/blob/06690177d2d5e11dbf82566297128d673b5a03a7/signalsets/v3/default.json
Licence: CC-BY-SA-4.0 (https://creativecommons.org/licenses/by-sa/4.0/), unlike the
Apache-2.0 code in the rest of this repository. See THIRD_PARTY_NOTICES.md.
Changes: signals rewritten as Field tuples (name, bit offset, length, scale, offset,
unit, sign, support bit, labels); descriptions, identifiers and display hints omitted.
Compound PIDs are displayed as labeled values by obd_fields.
"""

from q50diag.protocol.obd_fields import Field

PID_FIELDS = {
    0x01: (
        Field('Malfunction indicator lamp', 0, 1, 1.0, 0, '', choices=((0, 'Off'), (1, 'On'))),
        Field('Number of DTCs stored in this ECU', 1, 7, 1.0, 0, ''),
        Field('Comprehensive component monitoring ready', 9, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('Fuel system monitoring ready', 10, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('Misfire monitoring ready', 11, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('Comprehensive component monitoring supported', 13, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Fuel system monitoring supported', 14, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Misfire monitoring supported', 15, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('EGR system monitoring supported', 16, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Oxygen sensor heater monitoring supported', 17, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Oxygen sensor monitoring supported', 18, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('A/C system refrigerant monitoring supported', 19, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Secondary air system monitoring supported', 20, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Evaporative system monitoring supported', 21, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Heated catalyst monitoring supported', 22, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Catalyst monitoring supported', 23, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('EGR system monitoring ready', 24, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('Oxygen sensor heater monitoring ready', 25, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('Oxygen sensor monitoring ready', 26, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('A/C system refrigerant monitoring ready', 27, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('Secondary air system monitoring ready', 28, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('Evaporative system monitoring ready', 29, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('Heated catalyst monitoring ready', 30, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('Catalyst monitoring ready', 31, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
    ),
    0x02: (
        Field('DTC that caused required freeze frame data storage', 0, 16, 1.0, 0, ''),
    ),
    0x03: (
        Field('Fuel system 1 status', 0, 8, 1.0, 0, '', choices=(
            (0, 'OFF'),
            (1, 'OL'),
            (2, 'CL'),
            (4, 'OL-Drive'),
            (8, 'OL-Fault'),
            (16, 'CL-Fault'),
            (32, 'OL B2'),
            (64, 'OL-Drive B2'),
            (68, 'OL-Drive B1+B2'),
            (128, 'OL-Fault B2'),
        )),
        Field('Fuel system 2 status', 8, 8, 1.0, 0, '', choices=(
            (0, 'OFF'),
            (1, 'OL'),
            (2, 'CL'),
            (4, 'OL-Drive'),
            (8, 'OL-Fault'),
            (16, 'CL-Fault'),
            (32, 'OL B2'),
            (64, 'OL-Drive B2'),
            (68, 'OL-Drive B1+B2'),
            (128, 'OL-Fault B2'),
        )),
    ),
    0x04: (
        Field('Calculated engine load', 0, 8, 0.39215686274509803, 0, '%'),
    ),
    0x05: (
        Field('Engine coolant temperature', 0, 8, 1.0, -40, 'degC'),
    ),
    0x06: (
        Field('Short term fuel trim (bank 1)', 0, 8, 0.78125, -100, '%'),
    ),
    0x07: (
        Field('Long term fuel trim (bank 1)', 0, 8, 0.78125, -100, '%'),
    ),
    0x08: (
        Field('Short term fuel trim (bank 2)', 0, 8, 0.78125, -100, '%'),
    ),
    0x09: (
        Field('Long term fuel trim (bank 2)', 0, 8, 0.78125, -100, '%'),
    ),
    0x0A: (
        Field('Fuel pressure', 0, 8, 3.0, 0, 'kPa'),
    ),
    0x0B: (
        Field('Intake manifold absolute pressure', 0, 8, 1.0, 0, 'kPa'),
    ),
    0x0C: (
        Field('Engine RPM', 0, 16, 0.25, 0, 'rpm'),
    ),
    0x0D: (
        Field('Vehicle speed', 0, 8, 1.0, 0, 'km/h'),
    ),
    0x0E: (
        Field('Timing advance', 0, 8, 0.5, -64, 'deg'),
    ),
    0x0F: (
        Field('Intake air temperature', 0, 8, 1.0, -40, 'degC'),
    ),
    0x10: (
        Field('Air flow rate from mass air flow sensor', 0, 16, 0.01, 0, 'g/s'),
    ),
    0x11: (
        Field('Absolute throttle position', 0, 8, 0.39215686274509803, 0, '%'),
    ),
    0x12: (
        Field('Commanded secondary air status', 5, 3, 1.0, 0, '', choices=((1, 'UPS'), (2, 'DNS'), (4, 'OFF'), (8, 'DIAG'))),
    ),
    0x13: (
        Field('02S Bank 2, Sensor 4 present', 0, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('02S Bank 2, Sensor 3 present', 1, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('02S Bank 2, Sensor 2 present', 2, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('02S Bank 2, Sensor 1 present', 3, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('02S Bank 1, Sensor 4 present', 4, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('02S Bank 1, Sensor 3 present', 5, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('02S Bank 1, Sensor 2 present', 6, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('02S Bank 1, Sensor 1 present', 7, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
    ),
    0x14: (
        Field('O2S Output Voltage Bank 1, Sensor 1', 0, 8, 0.005, 0, 'V'),
        Field('SHRTFT associated with O2S11', 8, 8, 0.78125, -100, '%', unused=0xFF),
    ),
    0x15: (
        Field('O2S Output Voltage Bank 1, Sensor 2', 0, 8, 0.005, 0, 'V'),
        Field('SHRTFT associated with O2S12', 8, 8, 0.78125, -100, '%', unused=0xFF),
    ),
    0x16: (
        Field('O2S Output Voltage Bank 1, Sensor 3', 0, 8, 0.005, 0, 'V'),
        Field('SHRTFT associated with O2S13', 8, 8, 0.78125, -100, '%', unused=0xFF),
    ),
    0x17: (
        Field('O2S Output Voltage Bank 1, Sensor 4', 0, 8, 0.005, 0, 'V'),
        Field('SHRTFT associated with O2S14', 8, 8, 0.78125, -100, '%', unused=0xFF),
    ),
    0x18: (
        Field('O2S Output Voltage Bank 2, Sensor 1', 0, 8, 0.005, 0, 'V'),
        Field('SHRTFT associated with O2S21', 8, 8, 0.78125, -100, '%', unused=0xFF),
    ),
    0x19: (
        Field('O2S Output Voltage Bank 2, Sensor 2', 0, 8, 0.005, 0, 'V'),
        Field('SHRTFT associated with O2S22', 8, 8, 0.78125, -100, '%', unused=0xFF),
    ),
    0x1A: (
        Field('O2S Output Voltage Bank 2, Sensor 3', 0, 8, 0.005, 0, 'V'),
        Field('SHRTFT associated with O2S23', 8, 8, 0.78125, -100, '%', unused=0xFF),
    ),
    0x1B: (
        Field('O2S Output Voltage Bank 2, Sensor 4', 0, 8, 0.005, 0, 'V'),
        Field('SHRTFT associated with O2S24', 8, 8, 0.78125, -100, '%', unused=0xFF),
    ),
    0x1C: (
        Field('OBD requirements to which vehicle is designed', 0, 8, 1.0, 0, '', choices=(
            (1, 'OBD II'),
            (2, 'OBD'),
            (3, 'OBD & OBD II'),
            (4, 'OBD I'),
            (5, 'NO OBD'),
            (6, 'EOBD'),
            (7, 'EOBD & OBD II'),
            (8, 'EOBD & OBD'),
            (9, 'EOBD, OBD, OBD II'),
            (10, 'JOBD'),
            (11, 'JOBD & OBD II'),
            (12, 'JOBD & EOBD'),
            (13, 'JOBD, EOBD, OBD II'),
            (14, 'EURO IV B1'),
            (15, 'EURO V B2'),
            (16, 'EURO C'),
            (17, 'EMD'),
            (18, 'EMD+'),
            (19, 'HD OBD-C'),
            (20, 'HD OBD'),
            (21, 'WWH OBD'),
            (23, 'HD EOBD-I'),
            (24, 'HD EOBD-I N'),
            (25, 'HD EOBD-II'),
            (26, 'HD EOBD-II N'),
            (27, 'HD-ZEV'),
            (28, 'OBDBr-1'),
            (29, 'OBDBr-2'),
            (30, 'KOBD'),
            (31, 'IOBD-I-BS4'),
            (32, 'IOBD-II-BS4'),
            (33, 'HD EOBD-VI'),
            (34, 'OBD, OBD II and HD OBD'),
            (35, 'OBDBr-3'),
            (36, 'MC EOBD-I'),
            (37, 'MC EOBD-II'),
            (38, 'MC COBD-I'),
            (39, 'MC TOBD-I'),
            (40, 'MC JOBD-I'),
            (41, 'CN-OBD-6'),
            (42, 'OBDBr-P7'),
            (43, 'CN-HDOBD-VI'),
            (44, 'IOBD-I-BS6'),
            (45, 'IOBD-II-BS6'),
            (46, 'IHDOBD-BSVI'),
            (47, 'OBDBr-P8'),
            (48, 'HD-JOBD-II'),
            (49, 'HD-KOBD-II'),
            (50, 'CN-OROBD-IV'),
            (51, 'CARB ACC-II'),
            (52, 'MC JOBD-II'),
            (53, 'MC CARB OBD'),
            (54, 'MC EPA OBD'),
            (55, 'MC CARB & EPA OBD'),
            (56, 'HD ZEV CARB ZEP'),
            (57, 'CARB ACC-II & EPA TIER4'),
            (58, 'EPA TIER4'),
            (59, 'EPA HD'),
        )),
    ),
    0x1D: (
        Field('02S Bank 4, Sensor 2 present', 0, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('02S Bank 4, Sensor 1 present', 1, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('02S Bank 3, Sensor 2 present', 2, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('02S Bank 3, Sensor 1 present', 3, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('02S Bank 2, Sensor 2 present', 4, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('02S Bank 2, Sensor 1 present', 5, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('02S Bank 1, Sensor 2 present', 6, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('02S Bank 1, Sensor 1 present', 7, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
    ),
    0x1E: (
        Field('Power take off (PTO) status', 7, 1, 1.0, 0, '', choices=((0, 'Off'), (1, 'On'))),
    ),
    0x1F: (
        Field('Time since engine start', 0, 16, 1.0, 0, 's'),
    ),
    0x21: (
        Field('Distance traveled while MIL was activated', 0, 16, 1.0, 0, 'km'),
    ),
    0x22: (
        Field('Fuel pressure relative to manifold vacuum', 0, 16, 0.079, 0, 'kPa'),
    ),
    0x23: (
        Field('Fuel rail pressure', 0, 16, 10.0, 0, 'kPa'),
    ),
    0x24: (
        Field('Lambda value, Equivalence Ratio Bank 1, Sensor 1', 0, 16, 3.0517578125e-05, 0, ''),
        Field('Wide Range O2S Voltage, O2 Sensor Bank 1, Sensor 1', 16, 16, 0.0001220703125, 0, 'V'),
    ),
    0x25: (
        Field('Lambda value, Equivalence Ratio Bank 1, Sensor 2', 0, 16, 3.0517578125e-05, 0, ''),
        Field('Wide Range O2S Voltage, O2 Sensor Bank 1, Sensor 2', 16, 16, 0.0001220703125, 0, 'V'),
    ),
    0x26: (
        Field('Lambda value, Equivalence Ratio Bank 1, Sensor 3', 0, 16, 3.0517578125e-05, 0, ''),
        Field('Wide Range O2S Voltage, O2 Sensor Bank 1, Sensor 3', 16, 16, 0.0001220703125, 0, 'V'),
    ),
    0x27: (
        Field('Lambda value, Equivalence Ratio Bank 1, Sensor 4', 0, 16, 3.0517578125e-05, 0, ''),
        Field('Wide Range O2S Voltage, O2 Sensor Bank 1, Sensor 4', 16, 16, 0.0001220703125, 0, 'V'),
    ),
    0x28: (
        Field('Lambda value, Equivalence Ratio Bank 2, Sensor 1', 0, 16, 3.0517578125e-05, 0, ''),
        Field('Wide Range O2S Voltage, O2 Sensor Bank 2, Sensor 1', 16, 16, 0.0001220703125, 0, 'V'),
    ),
    0x29: (
        Field('Lambda value, Equivalence Ratio Bank 2, Sensor 2', 0, 16, 3.0517578125e-05, 0, ''),
        Field('Wide Range O2S Voltage, O2 Sensor Bank 2, Sensor 2', 16, 16, 0.0001220703125, 0, 'V'),
    ),
    0x2A: (
        Field('Lambda value, Equivalence Ratio Bank 2, Sensor 3', 0, 16, 3.0517578125e-05, 0, ''),
        Field('Wide Range O2S Voltage, O2 Sensor Bank 2, Sensor 3', 16, 16, 0.0001220703125, 0, 'V'),
    ),
    0x2B: (
        Field('Lambda value, Equivalence Ratio Bank 2, Sensor 4', 0, 16, 3.0517578125e-05, 0, ''),
        Field('Wide Range O2S Voltage, O2 Sensor Bank 2, Sensor 4', 16, 16, 0.0001220703125, 0, 'V'),
    ),
    0x2C: (
        Field('Commanded EGR', 0, 8, 0.39215686274509803, 0, '%'),
    ),
    0x2D: (
        Field('EGR error', 0, 8, 0.78125, -100, '%'),
    ),
    0x2E: (
        Field('Commanded evaporative purge', 0, 8, 0.39215686274509803, 0, '%'),
    ),
    0x2F: (
        Field('Fuel tank level', 0, 8, 0.39215686274509803, 0, '%'),
    ),
    0x30: (
        Field('Number of warm-ups since diagnostic trouble codes cleared', 0, 8, 1.0, 0, ''),
    ),
    0x31: (
        Field('Distance traveled since diagnostic trouble codes cleared', 0, 16, 1.0, 0, 'km'),
    ),
    0x32: (
        Field('Evap system vapor pressure', 0, 16, 0.00025, 0, 'kPa', signed=True),
    ),
    0x33: (
        Field('Barometric pressure', 0, 8, 1.0, 0, 'kPa'),
    ),
    0x34: (
        Field('Lambda value, Equivalence Ratio Bank 1, Sensor 1', 0, 16, 3.0517578125e-05, 0, ''),
        Field('Wide Range O2S Current, O2 Sensor Bank 1, Sensor 1', 16, 16, 0.00390625, -128, 'mA'),
    ),
    0x35: (
        Field('Lambda value, Equivalence Ratio Bank 1, Sensor 2', 0, 16, 3.0517578125e-05, 0, ''),
        Field('Wide Range O2S Current, O2 Sensor Bank 1, Sensor 2', 16, 16, 0.00390625, -128, 'mA'),
    ),
    0x36: (
        Field('Lambda value, Equivalence Ratio Bank 1, Sensor 3', 0, 16, 3.0517578125e-05, 0, ''),
        Field('Wide Range O2S Current, O2 Sensor Bank 1, Sensor 3', 16, 16, 0.00390625, -128, 'mA'),
    ),
    0x37: (
        Field('Lambda value, Equivalence Ratio Bank 1, Sensor 4', 0, 16, 3.0517578125e-05, 0, ''),
        Field('Wide Range O2S Current, O2 Sensor Bank 1, Sensor 4', 16, 16, 0.00390625, -128, 'mA'),
    ),
    0x38: (
        Field('Lambda value, Equivalence Ratio Bank 2, Sensor 1', 0, 16, 3.0517578125e-05, 0, ''),
        Field('Wide Range O2S Current, O2 Sensor Bank 2, Sensor 1', 16, 16, 0.00390625, -128, 'mA'),
    ),
    0x39: (
        Field('Lambda value, Equivalence Ratio Bank 2, Sensor 2', 0, 16, 3.0517578125e-05, 0, ''),
        Field('Wide Range O2S Current, O2 Sensor Bank 2, Sensor 2', 16, 16, 0.00390625, -128, 'mA'),
    ),
    0x3A: (
        Field('Lambda value, Equivalence Ratio Bank 2, Sensor 3', 0, 16, 3.0517578125e-05, 0, ''),
        Field('Wide Range O2S Current, O2 Sensor Bank 2, Sensor 3', 16, 16, 0.00390625, -128, 'mA'),
    ),
    0x3B: (
        Field('Lambda value, Equivalence Ratio Bank 2, Sensor 4', 0, 16, 3.0517578125e-05, 0, ''),
        Field('Wide Range O2S Current, O2 Sensor Bank 2, Sensor 4', 16, 16, 0.00390625, -128, 'mA'),
    ),
    0x3C: (
        Field('Catalyst temperature bank 1, sensor 1', 0, 16, 0.1, -40, 'degC'),
    ),
    0x3D: (
        Field('Catalyst temperature bank 2, sensor 1', 0, 16, 0.1, -40, 'degC'),
    ),
    0x3E: (
        Field('Catalyst temperature bank 1, sensor 2', 0, 16, 0.1, -40, 'degC'),
    ),
    0x3F: (
        Field('Catalyst temperature bank 2, sensor 2', 0, 16, 0.1, -40, 'degC'),
    ),
    0x41: (
        Field('Comprehensive component monitoring completed', 9, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('Fuel system monitoring completed', 10, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('Misfire monitoring completed', 11, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('Comprehensive component monitoring enabled', 13, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Fuel system monitoring enabled', 14, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Misfire monitoring enabled', 15, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('EGR system monitoring', 16, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Oxygen sensor heater monitoring', 17, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Oxygen sensor monitoring', 18, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('A/C system refrigerant monitoring', 19, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Secondary air system monitoring', 20, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Evaporative system monitoring', 21, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Heated catalyst monitoring', 22, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Catalyst monitoring', 23, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('EGR system monitoring completed', 24, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('Oxygen sensor heater monitoring completed', 25, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('Oxygen sensor monitoring completed', 26, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('A/C system refrigerant monitoring completed', 27, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('Secondary air system monitoring completed', 28, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('Evaporative system monitoring completed', 29, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('Heated catalyst monitoring completed', 30, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
        Field('Catalyst monitoring completed', 31, 1, 1.0, 0, '', choices=((0, 'Yes'), (1, 'No'))),
    ),
    0x42: (
        Field('Control module voltage', 0, 16, 0.001, 0, 'V'),
    ),
    0x43: (
        Field('Absolute load value', 0, 16, 0.39215686274509803, 0, '%'),
    ),
    0x44: (
        Field('Commanded equivalence ratio', 0, 16, 3.0517578125e-05, 0, ''),
    ),
    0x45: (
        Field('Relative throttle position', 0, 8, 0.39215686274509803, 0, '%'),
    ),
    0x46: (
        Field('Ambient air temperature', 0, 8, 1.0, -40, 'degC'),
    ),
    0x47: (
        Field('Absolute throttle position B', 0, 8, 0.39215686274509803, 0, '%'),
    ),
    0x48: (
        Field('Absolute throttle position C', 0, 8, 0.39215686274509803, 0, '%'),
    ),
    0x49: (
        Field('Accelerator pedal position D', 0, 8, 0.39215686274509803, 0, '%'),
    ),
    0x4A: (
        Field('Accelerator pedal position E', 0, 8, 0.39215686274509803, 0, '%'),
    ),
    0x4B: (
        Field('Accelerator pedal position F', 0, 8, 0.39215686274509803, 0, '%'),
    ),
    0x4C: (
        Field('Commanded throttle actuator control', 0, 8, 0.39215686274509803, 0, '%'),
    ),
    0x4D: (
        Field('Time run by the engine while MIL was activated', 0, 16, 60.0, 0, 's'),
    ),
    0x4E: (
        Field('Engine run time since diagnostic trouble codes cleared', 0, 16, 60.0, 0, 's'),
    ),
    0x4F: (
        Field('Maximum value for equivalence ratio', 0, 8, 1.0, 0, ''),
        Field('Maximum value for oxygen sensor voltage', 8, 8, 1.0, 0, 'V'),
        Field('Maximum value for oxygen sensor current', 16, 8, 1.0, 0, 'mA'),
        Field('Maximum value for intake manifold absolute pressure (MAP)', 24, 8, 10.0, 0, 'kPa'),
    ),
    0x50: (
        Field('Maximum value for air flow rate from mass air flow sensor', 0, 8, 10.0, 0, 'g/s'),
    ),
    0x51: (
        Field('Type of fuel currently being used by the vehicle', 0, 8, 1.0, 0, '', choices=(
            (1, 'GAS'),
            (2, 'METH'),
            (3, 'ETH'),
            (4, 'DSL'),
            (5, 'LPG'),
            (6, 'CNG'),
            (7, 'PROP'),
            (8, 'ELEC'),
            (9, 'BI_GAS'),
            (10, 'BI_METH'),
            (11, 'BI_ETH'),
            (12, 'BI_LPG'),
            (13, 'BI_CNG'),
            (14, 'BI_PROP'),
            (15, 'BI_ELEC'),
            (16, 'BI_MIX'),
            (17, 'HYB_GAS'),
            (18, 'HYB_ETH'),
            (19, 'HYB_DSL'),
            (20, 'HYB_ELEC'),
            (21, 'HYB_MIX'),
            (22, 'HYB_REG'),
            (23, 'BI_DSL'),
            (24, 'BI_NG'),
            (25, 'BI_DSL'),
            (26, 'NG'),
            (27, 'DSL_CNG'),
            (28, 'DSL_LNG'),
            (29, 'FC_H2'),
            (30, 'HICE_HHO'),
            (31, 'KERO'),
            (32, 'HFO'),
        )),
    ),
    0x52: (
        Field('Alcohol fuel percentage', 0, 8, 0.39215686274509803, 0, '%'),
    ),
    0x53: (
        Field('Absolute evap system vapor pressure', 0, 16, 0.005, 0, 'kPa'),
    ),
    0x54: (
        Field('Evap system vapor pressure (wide)', 0, 16, 0.001, 0, 'kPa', signed=True),
    ),
    0x55: (
        Field('Short term secondary O2 sensor fuel trim (bank 1)', 0, 8, 0.78125, -100, '%'),
    ),
    0x56: (
        Field('Long term secondary O2 sensor fuel trim (bank 1)', 0, 8, 0.78125, -100, '%'),
    ),
    0x57: (
        Field('Short term secondary O2 sensor fuel trim (bank 2)', 0, 8, 0.78125, -100, '%'),
    ),
    0x58: (
        Field('Long term secondary O2 sensor fuel trim (bank 2)', 0, 8, 0.78125, -100, '%'),
    ),
    0x59: (
        Field('Fuel rail pressure (absolute)', 0, 16, 10.0, 0, 'kPa'),
    ),
    0x5A: (
        Field('Accelerator pedal position\xa0(relative)', 0, 8, 0.39215686274509803, 0, '%'),
    ),
    0x5B: (
        Field('Hybrid battery pack remaining charge', 0, 8, 0.39215686274509803, 0, '%'),
    ),
    0x5C: (
        Field('Engine oil temperature', 0, 8, 1.0, -40, 'degC'),
    ),
    0x5D: (
        Field('Fuel injection timing', 0, 16, 0.0078125, -38665, 'deg'),
    ),
    0x5E: (
        Field('Engine fuel rate', 0, 16, 0.05, 0, 'L/h'),
    ),
    0x5F: (
        Field('Emission requirements to which vehicle is designed', 0, 8, 1.0, 0, '', choices=((14, 'EURO IV B1'), (15, 'EURO V B2'), (16, 'EURO C'), (17, 'EURO VI'))),
    ),
    0x61: (
        Field("Driver's demand engine torque", 0, 8, 1.0, -125, '%'),
    ),
    0x62: (
        Field('Actual engine torque', 0, 8, 1.0, -125, '%'),
    ),
    0x63: (
        Field('Engine reference torque', 0, 16, 1.0, 0, 'Nm'),
    ),
    0x64: (
        Field('Engine Percent Torque At Idle, Point 1', 0, 8, 1.0, -125, '%'),
        Field('Engine Percent Torque At Point 2', 8, 8, 1.0, -125, '%'),
        Field('Engine Percent Torque At Point 3', 16, 8, 1.0, -125, '%'),
        Field('Engine Percent Torque At Point 4', 24, 8, 1.0, -125, '%'),
        Field('Engine Percent Torque At Point 5', 32, 8, 1.0, -125, '%'),
    ),
    0x65: (
        Field('Recommended gear', 8, 4, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Glow plug lamp status', 12, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Manual Trans Neutral Gear Status', 13, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Auto Trans Neutral Drive Status', 14, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Power Take Off (PTO) Output Status', 15, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
    ),
    0x66: (
        Field('Mass Air Flow Sensor A', 8, 16, 0.03125, 0, 'g/s', support=7),
        Field('Mass Air Flow Sensor B', 24, 16, 0.03125, 0, 'g/s', support=6),
    ),
    0x67: (
        Field('Engine coolant temperature 1', 8, 8, 1.0, -40, 'degC', support=7),
        Field('Engine coolant temperature 2', 16, 8, 1.0, -40, 'degC', support=6),
    ),
    0x68: (
        Field('Intake air temperature, bank 1, sensor 1', 8, 8, 1.0, -40, 'degC', support=7),
        Field('Intake air temperature, bank 1, sensor 2', 16, 8, 1.0, -40, 'degC', support=6),
        Field('Intake air temperature, bank 1, sensor 3', 24, 8, 1.0, -40, 'degC', support=5),
        Field('Intake air temperature, bank 2, sensor 1', 32, 8, 1.0, -40, 'degC', support=4),
        Field('Intake air temperature, bank 2, sensor 2', 40, 8, 1.0, -40, 'degC', support=3),
        Field('Intake air temperature, bank 2, sensor 3', 48, 8, 1.0, -40, 'degC', support=2),
    ),
    0x69: (
        Field('Commanded EGR A duty cycle/position', 8, 8, 0.39215686274509803, 0, '%', support=7),
        Field('Actual EGR A duty cycle/position', 16, 8, 0.39215686274509803, 0, '%', support=6),
        Field('EGR A error', 24, 8, 0.78125, -100, '%', support=5),
        Field('Commanded EGR B duty cycle/position', 32, 8, 0.39215686274509803, 0, '%', support=4),
        Field('Actual EGR B duty cycle/position', 40, 8, 0.39215686274509803, 0, '%', support=3),
        Field('EGR B error', 48, 8, 0.78125, -100, '%', support=2),
    ),
    0x6A: (
        Field('Commanded intake air flow A control', 8, 8, 0.39215686274509803, 0, '%', support=7),
        Field('Relative intake air flow A position', 16, 8, 0.39215686274509803, 0, '%', support=6),
        Field('Commanded intake air flow B control', 24, 8, 0.39215686274509803, 0, '%', support=5),
        Field('Relative intake air flow B position', 32, 8, 0.39215686274509803, 0, '%', support=4),
    ),
    0x6B: (
        Field('Exhaust Gas Recirculation Temp Sensor A (Bank 1, Sensor 1)', 8, 8, 1.0, -40, 'degC', support=7),
        Field('Exhaust Gas Recirculation Temp Sensor C (Bank 1, Sensor 2)', 16, 8, 1.0, -40, 'degC', support=6),
        Field('Exhaust Gas Recirculation Temp Sensor B (Bank 2, Sensor 1)', 24, 8, 1.0, -40, 'degC', support=5),
        Field('Exhaust Gas Recirculation Temp Sensor D (Bank 2, Sensor 2)', 32, 8, 1.0, -40, 'degC', support=4),
    ),
    0x6C: (
        Field('Commanded Throttle Actuator A Control', 8, 8, 0.39215686274509803, 0, '%'),
        Field('Relative Throttle A Position', 16, 8, 0.39215686274509803, 0, '%'),
        Field('Commanded Throttle Actuator B Control', 24, 8, 0.39215686274509803, 0, '%'),
        Field('Relative Throttle B Position', 32, 8, 0.39215686274509803, 0, '%'),
    ),
    0x6D: (
        Field('Commanded Fuel Rail Pressure A', 8, 16, 10.0, 0, 'kPa', support=7),
        Field('Fuel Rail Pressure A', 24, 16, 10.0, 0, 'kPa', support=6),
        Field('Fuel Rail Temperature A', 40, 8, 1.0, -40, 'degC', support=5),
        Field('Commanded Fuel Rail Pressure B', 48, 16, 10.0, 0, 'kPa', support=4),
        Field('Fuel Rail Pressure B', 64, 16, 10.0, 0, 'kPa', support=3),
        Field('Fuel Rail Temperature B', 80, 8, 1.0, -40, 'degC', support=2),
    ),
    0x6E: (
        Field('Commanded injection control pressure A', 8, 16, 10.0, 0, 'kPa', support=7),
        Field('Injection control pressure A', 24, 16, 10.0, 0, 'kPa', support=6),
        Field('Commanded injection control pressure B', 40, 16, 10.0, 0, 'kPa', support=5),
        Field('Injection control pressure B', 56, 16, 10.0, 0, 'kPa', support=4),
    ),
    0x70: (
        Field('Commanded boost pressure A', 8, 16, 0.03125, 0, 'kPa', support=7),
        Field('Boost pressure sensor A', 24, 16, 0.03125, 0, 'kPa', support=6),
        Field('Commanded boost pressure B', 40, 16, 0.03125, 0, 'kPa', support=4),
        Field('Boost pressure sensor B', 56, 16, 0.03125, 0, 'kPa', support=3),
        Field('Boost bressure A control status', 76, 2, 1.0, 0, '', support=2, choices=((1, 'BP_B_OL'), (2, 'BP_B_CL'), (3, 'BP_B_FAULT'))),
        Field('Boost bressure A control status', 78, 2, 1.0, 0, '', support=5, choices=((1, 'BP_A_OL'), (2, 'BP_A_CL'), (3, 'BP_A_FAULT'))),
    ),
    0x71: (
        Field('Commanded variable geometry turbo A position', 8, 8, 0.39215686274509803, 0, '%', support=7),
        Field('Variable geometry turbo A position', 16, 8, 0.39215686274509803, 0, '%', support=6),
        Field('Commanded variable geometry turbo B position', 24, 8, 0.39215686274509803, 0, '%', support=4),
        Field('Variable geometry turbo B position', 32, 8, 0.39215686274509803, 0, '%', support=3),
        Field('Variable geometry turbo B control status', 44, 2, 1.0, 0, '', support=2, choices=((1, 'VGT_B_OL'), (2, 'VGT_B_CL'), (3, 'VGT_B_FAULT'))),
        Field('Variable geometry turbo A control status', 46, 2, 1.0, 0, '', support=5, choices=((1, 'VGT_A_OL'), (2, 'VGT_A_CL'), (3, 'VGT_A_FAULT'))),
    ),
    0x72: (
        Field('Commanded wastegate A position', 8, 8, 0.39215686274509803, 0, '%', support=7),
        Field('Wastegate A position', 16, 8, 0.39215686274509803, 0, '%', support=6),
        Field('Commanded wastegate B position', 24, 8, 0.39215686274509803, 0, '%', support=5),
        Field('Wastegate B position', 32, 8, 0.39215686274509803, 0, '%', support=4),
    ),
    0x73: (
        Field('Exhaust pressure bank 1, sensor 1', 8, 16, 0.01, 0, 'kPa', support=7),
        Field('Exhaust pressure bank 2, sensor 1', 24, 16, 0.01, 0, 'kPa', support=6),
    ),
    0x74: (
        Field('Turbocharger A speed', 8, 16, 10.0, 0, 'rpm', support=7),
        Field('Turbocharger B speed', 24, 16, 10.0, 0, 'rpm', support=6),
    ),
    0x75: (
        Field('Turbocharger A compressor inlet temperature', 8, 8, 1.0, -40, 'degC', support=7),
        Field('Turbocharger A compressor outlet temperature', 16, 8, 1.0, -40, 'degC', support=6),
        Field('Turbocharger A turbine inlet temperature', 24, 16, 0.1, -40, 'degC', support=5),
        Field('Turbocharger A turbine outlet temperature', 40, 16, 0.1, -40, 'degC', support=4),
    ),
    0x76: (
        Field('Turbocharger B compressor inlet temperature', 8, 8, 1.0, -40, 'degC', support=7),
        Field('Turbocharger B compressor outlet temperature', 16, 8, 1.0, -40, 'degC', support=6),
        Field('Turbocharger B turbine inlet temperature', 24, 16, 0.1, -40, 'degC', support=5),
        Field('Turbocharger B turbine outlet temperature', 40, 16, 0.1, -40, 'degC', support=4),
    ),
    0x77: (
        Field('Charge air cooler temperature bank 1, sensor 1', 8, 8, 1.0, -40, 'degC', support=7),
        Field('Charge air cooler temperature bank 1, sensor 2', 16, 8, 1.0, -40, 'degC', support=6),
        Field('Charge air cooler temperature bank 2, sensor 1', 24, 8, 1.0, -40, 'degC', support=5),
        Field('Charge air cooler temperature bank 2, sensor 2', 32, 8, 1.0, -40, 'degC', support=4),
    ),
    0x78: (
        Field('Exhaust gas temperature bank 1, sensor 1', 8, 16, 0.1, -40, 'degC', support=7),
        Field('Exhaust gas temperature bank 1, sensor 2', 24, 16, 0.1, -40, 'degC', support=6),
        Field('Exhaust gas temperature bank 1, sensor 3', 40, 16, 0.1, -40, 'degC', support=5),
        Field('Exhaust gas temperature bank 1, sensor 4', 56, 16, 0.1, -40, 'degC', support=4),
    ),
    0x79: (
        Field('Exhaust gas temperature bank 2, sensor 1', 8, 16, 0.1, -40, 'degC', support=7),
        Field('Exhaust gas temperature bank 2, sensor 2', 24, 16, 0.1, -40, 'degC', support=6),
        Field('Exhaust gas temperature bank 2, sensor 3', 40, 16, 0.1, -40, 'degC', support=5),
        Field('Exhaust gas temperature bank 2, sensor 4', 56, 16, 0.1, -40, 'degC', support=4),
    ),
    0x7A: (
        Field('Particulate filter bank 1 delta pressure', 8, 16, 0.01, 0, 'kPa', signed=True, support=7),
        Field('Particulate filter bank 1 inlet pressure', 24, 16, 0.01, 0, 'kPa', support=6),
        Field('Particulate filter bank 1 outlet pressure', 40, 16, 0.01, 0, 'kPa', support=5),
    ),
    0x7B: (
        Field('Particulate filter bank 2 delta pressure', 8, 16, 0.01, 0, 'kPa', signed=True, support=7),
        Field('Particulate filter bank 2 inlet pressure', 24, 16, 0.01, 0, 'kPa', support=6),
        Field('Particulate filter bank 2 outlet pressure', 40, 16, 0.01, 0, 'kPa', support=5),
    ),
    0x7C: (
        Field('Particulate filter bank 1 inlet temperature', 8, 16, 0.1, -40, 'degC', support=7),
        Field('Particulate filter bank 1 outlet temperature', 24, 16, 0.1, -40, 'degC', support=6),
        Field('Particulate filter bank 2 inlet temperature', 40, 16, 0.1, -40, 'degC', support=5),
        Field('Particulate filter bank 2 outlet temperature', 56, 16, 0.1, -40, 'degC', support=4),
    ),
    0x7E: (
        Field('NTE deficiency for PM active area', 4, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Inside manufacturer-specific PM NTE carve-out area', 5, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Outside PM control area', 6, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
        Field('Inside PM control area', 7, 1, 1.0, 0, '', choices=((0, 'No'), (1, 'Yes'))),
    ),
    0x7F: (
        Field('Total Engine Run Time', 8, 32, 1.0, 0, 's', support=7),
        Field('Total Idle Run Time', 40, 32, 1.0, 0, 's', support=6),
        Field('Total Run Time With PTO Active', 72, 32, 1.0, 0, 's', support=5),
    ),
}
