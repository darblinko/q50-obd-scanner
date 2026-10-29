# Third party notices

## OBDb SAEJ1979 signal set

The SAE J1979 field tables in two files of this repository are adapted from the OBDb SAEJ1979 signal set:

| File | Contents |
|---|---|
| `q50diag/protocol/obd_pid_data.py` | Service 01 PIDs 0x01 to 0x7F |
| `q50diag/protocol/obd_extended_data.py` | Service 01 PIDs 0x81 to 0xDC |

- Source: https://github.com/OBDb/SAEJ1979, file `signalsets/v3/default.json`
- Revision: commit 06690177d2d5e11dbf82566297128d673b5a03a7 (2026-10-10)
- Authors: the OBDb SAEJ1979 contributors (https://github.com/OBDb/SAEJ1979/graphs/contributors)
- Licence: Creative Commons Attribution-ShareAlike 4.0 International (CC-BY-SA-4.0), https://creativecommons.org/licenses/by-sa/4.0/. The full text is in [LICENSES/CC-BY-SA-4.0.txt](LICENSES/CC-BY-SA-4.0.txt).
- Disclaimer: the source material is provided as-is, without warranties, as stated in sections 5 and 6 of the licence.

Changes made to the source material:

1. Each JSON signal was rewritten as a Python `Field` (see `q50diag/protocol/obd_fields.py`) holding the signal name, bit offset, bit length, scale, offset, unit and, where present, the sign flag, support bit, value labels and the raw value meaning "not used".
2. `mul`, `div` and `add` were folded into a single scale and offset. OBDb unit identifiers were replaced with display units (for example `rpm`, `%`); `offon`, `yesno` and `noyes` signals and `map` enumerations became labeled choices.
3. Signal descriptions, identifiers, paths, suggested metrics, polling frequencies and `min`/`max` display hints were omitted.
4. Signals were grouped by PID number into the dictionaries `PID_FIELDS` and `EXTENDED_FIELDS`.

These two adapted files are licensed under CC-BY-SA-4.0, as section 3(b) of that licence requires. The rest of the repository, including the code in `obd_fields.py` that decodes the tables, is licensed under Apache-2.0 ([LICENSE](LICENSE)).

`q50diag/protocol/obd2.py` (Apache-2.0) adds fields for PIDs 0x6F, 0x7D, 0x87, 0x98 and 0x99, which are absent from the OBDb catalog. Their field names are this project's own wording and no SAE document text is reproduced. They are not adapted material. The same module sets a wide-range scale on the adapted PID 0x6B fields when it is imported.
