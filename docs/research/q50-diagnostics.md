# Q50 diagnostic research (2026-10-17)

Vehicle: 2017 Infiniti Q50 3.0t, no DAS (steering menu offers Standard/Sport only), no
power-steering fluid reservoir. 3.0t models have rack electronic power steering as standard
(MotorAuthority 2016 Q50 Red Sport first drive; The Peninsula Qatar 2016-10-22).

## Protocol

- ISO 15765-4 CAN, 11-bit, 500 kbps on DLC pins 6/14.
- Nissan KWP2000-style services over CAN: `10 C0` (Nissan CONSULT session) before `21 xx`
  (local ID) and `22 xxxx` (2-byte ID) reads. `10 81` default session. openpilot reads
  firmware with `10 81` then `21 83` (commaai/opendbc `opendbc/car/nissan/values.py`).
- OBDb test file naming `221205|din=C0,dout=81` suggests: enter C0, read, return to 81 (inference).
- Real capture: 2016 Q50 ECM answered `7E8 05 62 12 05 00 00` to `22 1205` in session C0
  (OBDb/INFINITI-Q50 test cases).
- KWP DTC services: `18` (ReadDTCByStatus, positive `58`), `17` (`57`), `14` clear.
  Expected layout `58 N [hi lo status] x N`; top 2 bits of `hi` select P/C/B/U
  (00/01/10/11). Unverified on Nissan; verify against a known code.
- UDS `19 02 mask` returns `59 02 availMask [d1 d2 d3 status]...` on newer Nissans only.

## Diagnostic IDs (request / response)

| Module | Req | Resp | Evidence |
|---|---|---|---|
| ECM | 7E0 | 7E8 | OBDb Q50 test cases (confirmed) |
| TCM | 7E1 | 7E9 | OBDb Q50 (unverified) |
| ABS/VDC | 740 | 760 | OBDb Q50, opendbc |
| EPS | 742 | 762 | opendbc Nissan fingerprints; Q50 not confirmed |
| METER | 743 | 763 | OBDb Q50 |
| HVAC | 744 | 764 | Leaf, OBDb Nissan |
| BCM | 745 | 765 | Leaf |
| AV / Multi AV | 747 | 767 | Leaf |
| AWD | 748 | 768 | OBDb Nissan (session C0) |
| IPDM E/R | 74D | 76D | Leaf |
| AIR BAG | 752 | 772 | Leaf |
| TPMS | 758 | 778 | OBDb Nissan |
| Steering angle unit | 75C | 77C | OBDb Nissan, model unspecified |
| Front camera / ADAS | 707 | 727 | opendbc, OBDb Q50 |
| Radar | 723 | 735 | OBDb Nissan |

Pattern: response = request + 0x20 for 0x7xx body/chassis; ECM/TCM use + 8.
nullperez/infiniti-q50-testbench saw UDS traffic on 0x71D/0x72D on a Q50 main bus.

## Known Nissan identifiers (sources: OBDb/INFINITI-Q50 `signalsets/v3/default.json`,
## elvisfox/obd2ools `pids/nissan.py`)

- ECM (7E0, session C0): `22 1101` coolant, x-50 degC; `22 1201` RPM, x12.5;
  `22 1209` MAF x0.01 g/s; `22 120D`/`22 120E` accelerator pedal sensor V x0.005;
  `22 121A` vehicle speed x0.1 km/h; `22 1124..2095` range in OBDb (73 commands total).
- TCM (7E1): `21 01/03/10/30`, `22 11xx/12xx` (many look CVT-derived; Q50 is 7-speed AT).
- ABS (740): `22 1115` accel pedal, `22 1208` steering angle signed 16-bit /10 deg,
  `22 1209` master cylinder pressure.
- METER (743): `21 01`, `22 0E01` odometer, `22 0E25..0E28` tire pressure /4 psi.
- Steering angle unit (75C): `21 01`, angle /10.

No public source gives EPS or DAST DIDs for the Q50. They must be found by scanning
identifiers on the EPS module (explorer feature).

## Broadcast frames (nullperez/infiniti-q50-canbus, 2019 Q50, `q50_main.dbc`)

- 0x002: steering angle, little-endian signed, x -0.1 deg.
- 0x300: power-steering assist magnitude (uncalibrated).
- 0x5E4: steering warning lamp.
- 0x174: gear (7-speed).

## Adapter (vLinker FS USB)

- FTDI USB serial (VID 0x0403; PID not published). Firmware updater uses 115200 baud;
  vendor claims up to 3 Mbps. Plain ELM327 defaults are 38400 or 9600.
- Reports as ELM327 v2.2-class. Supports ATSH, ATCRA, ATCF/ATCM, ATFC SH/SD/SM, ATCAF,
  ATAL, ATMA, ATCP, ATSP6. STN `ST` commands unconfirmed on FS.
- Vendor: 4128-byte OBD requests, 8192-byte serial buffer.
- Update firmware before use (FORScan compatibility issue history).

## Open questions

- Whether the EPS answers on 742/762 on this car.
- Whether any modules sit behind a second CAN on DLC pins 12/13 (Nissan TSB NTB10-066B
  mentions 2 CAN systems on certain models) and are unreachable by the vLinker FS.

## EPS follow-up (non-DAS research pass, 2026-10-17)

- No NHTSA recall or TSB covers EPS on a 2016-2019 non-DAS Q50. The only steering recall in
  that range is 16V430 (DAS only). ITB18-021 and ITB19-001 are DAS only (calemonlaw.com TSB
  lists for 2017 and 2018 Q50).
- NHTSA complaints: 11174708 (2019 Q50, no power steering, "battery low"); 11197207 (2017 Q50,
  power steering failed twice).
- Generic Nissan EPS DTCs, not Q50-confirmed (automotivetechinfo.com, 2015-03, "A turn for
  the better: Nissan electronic power steering"):

| Code | Meaning |
|---|---|
| C1601 | Supply voltage below 8.5 V or above 18.5 V |
| C1604 | Torque sensor |
| C1606 | EPS motor |
| C1607 / C1608 | EPS ECU internal |
| C1609 | Vehicle speed signal |
| C160A | Overheat protection activated (not a fault) |
| C1613 | Torque sensor calibration needed |
| U1000 | CAN communication lost for about 2 s |

- Nissan EPS data-monitor items: ASSIST LEVEL (normally 100 %, falls when assist is limited),
  TORQUE SENSOR (Nm), MOTOR CURRENT (A), MOTOR VOL (V) (Scribd STC manual copy, unverified).
- Generic heavy-steering causes on Nissan EPS: low 12 V supply, overheat protection, CAN loss,
  torque sensor drift.
- Broadcast 0x300 byte 0: "steer effort", probable EPS assist, uncalibrated. Broadcast 0x5E4
  bit 2: power-steering warning lamp (confirmed in nullperez/infiniti-q50-canbus).
- More ECM DIDs (elvisfox/obd2ools): 1204/1205 MAF voltage B1/B2 x0.005 V; 1123-1126 A/F
  trims x-100 %. OBDb Q50: 2095 oil temperature x0.75-48 degC.
- Nissan writes newer DTCs as `C1B90-00` (ITB19-001), suggesting a 3-byte UDS DTC with a
  failure-type byte. Probe both 0x18 and 0x19 on each module.
- Suggested EPS probe: `10 C0` then `21 83` on 0x742; scan `21 00-FF` and `22 11xx/12xx`.
- vLinker FS: FT-X series FTDI parts use PID 0x6015 (Linux ftdi_sio_ids.h); actual PID unknown.
  STN `STI`/`STDI` unverified on FS; probe at runtime.
