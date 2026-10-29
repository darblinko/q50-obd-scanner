import csv

from q50diag.core.types import Reading
from q50diag.recorder import COLUMNS, Recorder, list_recordings, load_recording, recording_filename


class FakeClock:
    def __init__(self, start: float = 1_760_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def test_filename_uses_local_timestamp():
    name = recording_filename(1_760_000_000.0)
    assert len(name) == len("YYYYMMDD-HHMMSS.csv")
    assert name.endswith(".csv") and name[8] == "-"


def test_writes_long_format_and_events(tmp_path):
    clock = FakeClock()
    recorder = Recorder(tmp_path, clock=clock)
    path = recorder.start()
    assert recorder.is_recording and path.parent == tmp_path
    recorder.write_reading(Reading("obd.01.0C", 812.5, "rpm", clock.now), "Engine RPM", "ECM")
    recorder.write_reading(Reading("bcast.5E4.ps_warning_lamp", True, "", clock.now + 0.1), "Lamp", "BROADCAST")
    recorder.write_reading(Reading("EPS.21.01", None, "A", clock.now + 0.2, error="NO DATA"), "Motor", "EPS")
    clock.now += 5
    assert recorder.elapsed() == 5
    stamp = recorder.mark_event("steering stiff now")
    recorder.stop()
    assert not recorder.is_recording and recorder.elapsed() == 0.0

    rows = list(csv.reader(path.open()))
    assert tuple(rows[0]) == COLUMNS
    assert rows[1] == [f"{clock.now - 5:.3f}", "DATA", "obd.01.0C", "Engine RPM", "812.5", "rpm", "ECM", ""]
    assert rows[2][4] == "1"
    assert rows[3][4] == "" and rows[3][7] == "NO DATA"
    assert rows[4] == [f"{stamp:.3f}", "EVENT", "event", "steering stiff now", "", "", "", ""]
    assert recorder.rows_written == 4


def test_not_recording_ignores_writes_but_returns_event_time(tmp_path):
    recorder = Recorder(tmp_path, clock=FakeClock(100.0))
    recorder.write_reading(Reading("k", 1, "", 1.0))
    assert recorder.mark_event("x") == 100.0
    assert list(tmp_path.iterdir()) == []


def test_unique_path_when_started_twice_in_same_second(tmp_path):
    clock = FakeClock()
    recorder = Recorder(tmp_path, clock=clock)
    first = recorder.start()
    second = recorder.start()
    recorder.stop()
    assert first != second and first.exists() and second.exists()


def test_load_round_trip(tmp_path):
    clock = FakeClock()
    recorder = Recorder(tmp_path, clock=clock)
    path = recorder.start()
    for index in range(5):
        recorder.write_reading(Reading("adapter.voltage", 12.0 + index / 10, "V", clock.now + index), "Battery", "ADAPTER")
    recorder.write_reading(Reading("obd.vin", "JN1ABC", "", clock.now), "VIN", "ECM")
    recorder.write_reading(Reading("adapter.voltage", None, "V", clock.now + 9, error="timeout"), "Battery", "ADAPTER")
    recorder.mark_event("heavy", clock.now + 2.5)
    recorder.stop()
    with path.open("a") as handle:
        handle.write("garbage,row\n")

    recording = load_recording(path)
    voltage = recording.series["adapter.voltage"]
    assert voltage.values == [12.0, 12.1, 12.2, 12.3, 12.4]
    assert voltage.name == "Battery" and voltage.unit == "V" and voltage.module == "ADAPTER"
    assert voltage.errors == 1
    assert recording.series["obd.vin"].text_values == ["JN1ABC"]
    assert [(e.timestamp, e.text) for e in recording.events] == [(clock.now + 2.5, "heavy")]
    assert recording.start == clock.now and recording.end == clock.now + 9
    assert recording.bad_rows == 1


def test_list_recordings_newest_first(tmp_path):
    (tmp_path / "a.csv").write_text("x")
    (tmp_path / "b.csv").write_text("x")
    (tmp_path / "notes.txt").write_text("x")
    import os

    os.utime(tmp_path / "a.csv", (1, 1))
    assert [p.name for p in list_recordings(tmp_path)] == ["b.csv", "a.csv"]
    assert list_recordings(tmp_path / "missing") == []
