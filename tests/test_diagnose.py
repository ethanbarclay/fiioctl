"""The diagnose report: descriptor sizes, the probe matrix, the summary. No hardware."""

from fiioctl import diagnose, registry

# usage page 0xff00, application collection; report 7 as 63 bytes in and out;
# report 1 as a 2-byte consumer-control input; a 32-byte feature report 9
DESCRIPTOR = bytes([
    0x06, 0x00, 0xFF, 0x09, 0x01, 0xA1, 0x01,
    0x85, 0x07, 0x75, 0x08, 0x95, 0x3F, 0x09, 0x01, 0x81, 0x02, 0x09, 0x01, 0x91, 0x02,
    0x85, 0x09, 0x95, 0x20, 0x09, 0x01, 0xB1, 0x02,
    0xC0,
    0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01,
    0x85, 0x01, 0x75, 0x10, 0x95, 0x01, 0x09, 0xE9, 0x81, 0x00,
    0xC0,
])


def test_report_sizes_come_from_the_descriptor():
    layout = diagnose.parse_descriptor(DESCRIPTOR)
    assert layout["usage_pages"] == ["0xff00", "0xc"]
    assert layout["reports"] == [
        {"id": 1, "kind": "input", "bytes": 2},
        {"id": 7, "kind": "input", "bytes": 63},
        {"id": 7, "kind": "output", "bytes": 63},
        {"id": 9, "kind": "feature", "bytes": 32},
    ]


def test_push_and_pop_restore_the_report_size():
    rd = bytes([0x75, 0x08, 0x95, 0x04, 0xA4, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
                0xB4, 0x91, 0x02])
    assert diagnose.parse_descriptor(rd)["reports"] == [
        {"id": 0, "kind": "input", "bytes": 1},
        {"id": 0, "kind": "output", "bytes": 4},
    ]


class Picky:
    """Answers a GET only when the write is exactly `length` bytes."""

    def __init__(self, length):
        self.length = length
        self.sent = []
        self.queue = []

    def write(self, out):
        self.sent.append(bytes(out))
        if len(out) == self.length:
            self.queue.append(list(bytes([7, 0xBB, 0x0B, 0, 0, out[5], 1, 3, 0, 0xEE])))
        return len(out)

    def read(self, _size, timeout_ms=0):
        return self.queue.pop(0) if self.queue else []


def test_the_probe_sends_only_gets_and_finds_the_length_that_works():
    handle = Picky(length=9)
    rows = diagnose.probe(handle, [7], listen=0.01, pause=0)
    assert all(p[1:3] == bytes([0xBB, 0x0B]) for p in handle.sent)
    assert not {p[5] for p in handle.sent} & set(registry.DESTRUCTIVE)
    answered = {(r["name"], r["length"]) for r in rows if r["answered"]}
    assert answered == {(name, 9) for name in diagnose.PROBE_CMDS}


def _result(probe_rows):
    return {"environment": {"fiioctl": "x", "backend": "hidraw", "platform": "Linux"},
            "interfaces": [{"path": "/dev/hidraw3", "product": "FIIO FG3", "interface": 3,
                            "usage_page": "0xc",
                            "descriptor": diagnose.parse_descriptor(DESCRIPTOR),
                            "probe": probe_rows}]}


def test_the_summary_names_the_length_that_was_answered():
    rows = diagnose.probe(Picky(length=9), [7], listen=0.01, pause=0)
    text = "\n".join(diagnose.summary(_result(rows)))
    assert "output id 7 = 63 bytes" in text
    assert "answered at: report id 7 length 9" in text


def test_the_summary_says_when_nothing_answered():
    rows = diagnose.probe(Picky(length=0), [7], listen=0.01, pause=0)
    assert "no probe got an answer" in "\n".join(diagnose.summary(_result(rows)))
