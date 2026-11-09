"""Offline tests: framing, codecs and profile parsing need no hardware."""

import pytest

from fiioctl import eqdb, proto, registry


def test_frame_layout():
    pkt = proto.frame(proto.SET, 23, b"\x00\x1e")
    assert pkt == bytes([0xAA, 0x0A, 0x00, 0x00, 23, 2, 0x00, 0x1E, 0x00, 0xEE])


def test_frame_empty_payload():
    assert proto.frame(proto.GET, 11) == bytes([0xBB, 0x0B, 0, 0, 11, 0, 0x00, 0xEE])


def test_parse_reply_with_report_id_prefix():
    reply = bytes([0x07, 0xBB, 0x0B, 0, 0, 24, 1, 10, 0x00, 0xEE])
    assert proto.parse(reply) == (24, b"\x0a")


def test_parse_reply_with_sequence_byte():
    """Byte 3 of a real reply is a rolling counter, not zero (KA15 fw 1.1.6)."""
    reply = bytes([0x07, 0xBB, 0x0B, 0x00, 0x22, 0x0B, 0x03, 1, 1, 6, 0xEE])
    assert proto.parse(reply) == (11, b"\x01\x01\x06")
    assert proto.CODECS["version"][1](b"\x01\x01\x06") == "1.1.6"


def test_parse_ignores_trailing_junk_after_terminator():
    """Real reports carry stale bytes after 0xEE; length must win."""
    reply = bytes([0x07, 0xBB, 0x0B, 0x00, 0x23, 0x0B, 0x03, 1, 1, 6, 0xEE,
                   0x80, 0x00, 0x47, 0x00, 0x58, 0xEE])
    assert proto.parse(reply) == (11, b"\x01\x01\x06")


def test_parse_rejects_non_reply():
    assert proto.parse(b"\x00" * 16) is None


@pytest.mark.parametrize("value", [0.0, 5.5, -6.0, 12.0, -12.0])
def test_gain_codec_roundtrip(value):
    enc, dec = proto.CODECS["x10"]
    assert dec(enc(value)) == pytest.approx(value)


def test_q_codec_roundtrip():
    enc, dec = proto.CODECS["x100"]
    assert dec(enc(3.46)) == pytest.approx(3.46)


def test_band_roundtrip():
    payload = proto.enc_band(2, freq=9140, gain=5.6, q=3.46, filter_type="peak")
    assert len(payload) == 8
    out = proto.dec_band(payload)
    assert out == {"index": 2, "gain": pytest.approx(5.6), "freq": 9140,
                   "q": pytest.approx(3.46), "filter": "peak"}


def test_band_negative_gain_is_twos_complement():
    payload = proto.enc_band(0, 180, -3.3, 0.57, "peak")
    assert payload[1:3] == (0x10000 - 33).to_bytes(2, "big")
    assert proto.dec_band(payload)["gain"] == pytest.approx(-3.3)


def test_balance_codec_matches_web_app():
    enc, dec = proto.CODECS["balance"]
    assert enc(5) == b"\x00\x05"
    assert enc(0) == b"\x00\x00"
    assert enc(-5) == b"\xfb\x00"
    assert dec(enc(5)) == 5
    assert dec(enc(-5)) == -5


def test_ka15_registry_facts():
    spec = registry.match("FIIO KA15")
    assert spec.report_id == 7
    assert spec.name_offset == 7
    assert spec.presets["user1"] == 7
    assert spec.eq_off == 10
    assert spec.gain_range == (-12.0, 12.0)
    # observed on firmware 1.1.6: cmd 26 never answers on this model
    assert "eq-switch" in spec.unsupported


def _dry(spec=None):
    """A Dac that builds packets but writes nowhere."""
    from fiioctl.device import Dac
    return Dac(None, {"product_string": "FIIO KA15"}, spec or registry.KA15, dry=True)


def test_enum_rejects_values_the_firmware_would_ignore():
    """`set language 2` used to report success while the device kept its value."""
    from fiioctl.device import FiioError
    dac = _dry()
    with pytest.raises(FiioError, match="chinese"):
        dac.set("language", 2)
    with pytest.raises(FiioError):
        dac.set("language", "klingon")


def test_enum_accepts_names_and_valid_numbers():
    dac = _dry()
    assert dac.set("language", "english") == 1
    assert dac.set("language", "chinese") == 0
    assert dac.set("language", 1) == 1
    assert dac.set("language", 0) == 0


def test_preset_rejects_slots_the_device_does_not_have():
    """`set eq-preset 99` used to report 99 while the device clamped to 10."""
    from fiioctl.device import FiioError
    dac = _dry()
    with pytest.raises(FiioError, match="not a slot"):
        dac.set("eq-preset", 99)
    assert dac.set("eq-preset", 9) == 9


def test_preset_accepts_the_names_the_device_advertises():
    """`set eq-preset user1` must work, since the error text promises names do."""
    dac = _dry()
    assert dac.set("eq-preset", "user1") == 7
    assert dac.set("eq-preset", "USER1") == 7
    assert dac.set("eq-preset", "off") == 10
    assert dac.set("eq-preset", 7) == 7


def test_preset_name_error_lists_the_names():
    from fiioctl.device import FiioError
    dac = _dry()
    with pytest.raises(FiioError, match="user1"):
        dac.set("eq-preset", "nonsense")


def test_dry_run_never_tries_to_read_back():
    """--dry-run builds packets with no device, so verification must be skipped."""
    dac = _dry()
    assert dac.set("volume", 30, verify=True) == 30
    assert dac.set("language", "english", verify=True) == 1


def test_fractional_values_rejected_not_crashed():
    """`set brightness 5.5` used to raise a bare ValueError from the encoder."""
    from fiioctl.device import FiioError
    dac = _dry()
    for bad in ("5.5", 5.5, "abc", None):
        with pytest.raises(FiioError):
            dac.set("brightness", bad)
    for bad in ("30.5", 30.5):
        with pytest.raises(FiioError):
            dac.set("volume", bad)


def test_whole_numbers_accepted_in_any_form():
    dac = _dry()
    assert dac.set("brightness", 5) == 5
    assert dac.set("brightness", "5") == 5
    assert dac.set("brightness", 5.0) == 5


def test_fractional_still_allowed_where_the_wire_format_supports_it():
    """eq-gain is dB x10, so tenths are meaningful - do not over-restrict."""
    dac = _dry(registry.DEFAULT)
    assert dac.set("eq-gain", -6.5) == -6.5


def test_range_still_enforced():
    from fiioctl.device import FiioError
    dac = _dry()
    with pytest.raises(FiioError, match="outside"):
        dac.set("brightness", 11)
    assert dac.set("brightness", 10) == 10


def test_verify_reports_what_the_device_actually_holds():
    """A firmware that clamps must not be reported as having accepted the write."""
    from fiioctl.device import Dac, FiioError

    class Clamping(Dac):
        """Not dry: verification is skipped on a dry connection by design."""

        def send(self, cmd, data=b"", hdr=None):
            pass

        def get(self, name, timeout=None):
            return 10  # whatever was asked for, the device lands on 10

    dac = Clamping(None, {}, registry.KA15)
    assert dac.set("eq-preset", 10, verify=True) == 10
    with pytest.raises(FiioError, match="device holds 10"):
        dac.set("eq-preset", 9, verify=True)


def test_scan_never_probes_destructive_commands():
    """A GET should be harmless, but not if firmware acts on the command byte."""
    assert set(registry.DESTRUCTIVE) == {25, 27, 33}
    assert registry.COMMANDS["eq-save"].id in registry.DESTRUCTIVE
    assert registry.COMMANDS["factory-reset"].id in registry.DESTRUCTIVE


def test_diff_scans_reports_only_changes():
    from fiioctl.cli import diff_scans
    before = {"3": "00", "5": "01", "14": "05"}
    after = {"3": "00", "5": "02", "32": "01"}
    assert diff_scans(before, after) == [
        (5, "01", "02"),       # changed
        (14, "05", None),      # went silent
        (32, None, "01"),      # newly answering
    ]


def test_diff_scans_is_empty_when_nothing_moved():
    from fiioctl.cli import diff_scans
    assert diff_scans({"3": "00"}, {"3": "00"}) == []


def test_name_decode_stops_at_stale_bytes():
    """Observed on a KA15: the 8-byte name field keeps old PEQ bytes after the name."""
    # "M6" followed by 0x2710 (10000 Hz) and 0x0046 (Q 0.70) from an earlier write
    assert proto.dec_name(b"\x4d\x36\x27\x10\x00\x46\x02") == "M6'"
    assert proto.dec_name(b"DT177X\x02") == "DT177X"
    assert proto.dec_name(b"FD15\x00\x00\x00\x00") == "FD15"
    assert proto.dec_name(b"") == ""


def test_name_encode_pads_the_whole_field():
    """Short names must clear the field, or the leftovers read back as garbage."""
    assert proto.enc_name(2, "M6", 8) == b"\x02M6\x00\x00\x00\x00\x00\x00"
    assert len(proto.enc_name(0, "DT177X", 8)) == 9


def test_name_encode_truncates_to_the_field_width():
    assert proto.enc_name(0, "ABCDEFGHIJ", 8) == b"\x00ABCDEFGH"


def _dac(spec):
    """A Dac with no handle: snap_gain only consults the device spec."""
    from fiioctl.device import Dac
    return Dac(None, {}, spec)


def test_ka15_global_gain_is_whole_decibels():
    """Measured on firmware 1.1.6: the device truncates fractions toward zero."""
    assert registry.KA15.gain_step == 1.0


def test_snap_gain_rounds_to_the_device_resolution():
    dac = _dac(registry.KA15)
    assert dac.snap_gain(-6.4) == -6.0
    assert dac.snap_gain(-6.6) == -7.0
    assert dac.snap_gain(2.7) == 3.0
    assert dac.snap_gain(-7.0) == -7.0


def test_snap_gain_safe_mode_never_loses_attenuation():
    """A profile's pre-amp must not drift upward: that is what clips."""
    dac = _dac(registry.KA15)
    assert dac.snap_gain(-6.1, safe=True) == -7.0
    assert dac.snap_gain(-6.6, safe=True) == -7.0
    assert dac.snap_gain(-7.0, safe=True) == -7.0


def test_snap_gain_clamps_to_the_device_range():
    dac = _dac(registry.KA15)
    assert dac.snap_gain(-50) == -12.0
    assert dac.snap_gain(50) == 12.0


def test_snap_gain_leaves_tenths_alone_on_finer_devices():
    dac = _dac(registry.DEFAULT)
    assert dac.spec.gain_step == 0.1
    assert dac.snap_gain(-6.6) == -6.6


def test_filter_range_admits_zero():
    """The KA15 reports filter mode 0, so 1-7 would have been too narrow."""
    lo, hi = registry.COMMANDS["filter"].rng
    assert lo == 0 and hi >= 5


def test_adapter_devices_are_marked_unsupported():
    """These seven go through their own controller in the official app."""
    for key in registry.ADAPTER_DEVICES:
        assert registry.DEVICES[key].transport == "adapter", key


def test_adapter_devices_keep_their_catalogue_ids():
    """The catalogue is HTTP keyed by product id, so it works regardless of
    whether the control protocol does."""
    expected = {"FIIO BTR15": 24, "JADEAUDIO TRUSTBLU": 51, "FIIO FG3": 120,
                "FIIO JD1 GAMING": 407, "FIIO KG13": 409, "FIIO JK13": 411,
                "SNOWSKY GAMDONG": 412}
    for key, cloud in expected.items():
        assert registry.DEVICES[key].cloud_id == cloud, key


def test_no_device_error_mentions_serial_only_models(monkeypatch):
    """A K15 never appears in an HID enumeration, so the flag cannot explain it
    later - the not-found error has to."""
    from fiioctl import device as device_mod
    from fiioctl.device import Dac, FiioError

    monkeypatch.setattr(device_mod, "candidates", lambda: [])
    with pytest.raises(FiioError, match="FIIO K15"):
        Dac.open()


def test_models_using_lists_by_transport():
    assert registry.models_using("serial") == ["FIIO K15"]
    assert len(registry.models_using("adapter")) == len(registry.ADAPTER_DEVICES)
    assert "FIIO KA15" in registry.models_using("hid")


def test_k15_is_marked_serial():
    """The app refuses to drive a K15 without a serial port controller."""
    assert registry.DEVICES["FIIO K15"].transport == "serial"


def test_only_the_ka15_claims_hardware_verification():
    verified = [k for k, d in registry.DEVICES.items() if d.verified]
    assert verified == ["FIIO KA15"]


def test_hid_is_the_default_transport():
    assert registry.DEVICES["FIIO KA15"].transport == "hid"
    assert registry.DEFAULT.transport == "hid"


def test_unsupported_transports_have_an_explanation():
    from fiioctl.device import Dac
    declared = {d.transport for d in registry.DEVICES.values()} - {"hid"}
    assert declared <= set(Dac.UNSUPPORTED)
    assert declared == {"adapter", "serial"}


def test_open_refuses_a_device_whose_transport_is_not_implemented(monkeypatch):
    from fiioctl import device as device_mod
    from fiioctl.device import Dac, FiioError

    fake = {"product_string": "FIIO BTR15", "path": b"fake", "vendor_id": 0x2972,
            "usage_page": 0x000C}
    monkeypatch.setattr(device_mod, "candidates", lambda: [fake])

    with pytest.raises(FiioError, match="has not been tested"):
        Dac.open()


def test_force_gets_past_the_transport_gate(monkeypatch):
    """--force should fail for some other reason, not the transport check."""
    from fiioctl import device as device_mod
    from fiioctl.device import Dac, FiioError

    fake = {"product_string": "FIIO BTR15", "path": b"fake", "vendor_id": 0x2972,
            "usage_page": 0x000C}
    monkeypatch.setattr(device_mod, "candidates", lambda: [fake])

    with pytest.raises(FiioError) as caught:
        Dac.open(force=True)
    assert "has not been tested" not in str(caught.value)


def test_registry_loose_product_match():
    assert registry.match("FIIO KA15 ").name == "FIIO KA15"
    assert registry.match("JadeAudio JA11").report_id == 2
    assert registry.match("Some Unknown Thing").name == "unknown"


SAMPLE = """Preamp: -6.0 dB
Filter 1: ON LSC Fc 105 Hz Gain 5.5 dB Q 0.70
Filter 2: ON PK Fc 180 Hz Gain -3.3 dB Q 0.57
Filter 3: OFF PK Fc 500 Hz Gain 2.0 dB Q 1.00
Filter 4: ON HSC Fc 10000 Hz Gain -1.1 dB Q 0.70
"""


def test_parse_parametric_eq():
    prof = eqdb.parse(SAMPLE)
    assert prof["preamp"] == -6.0
    assert len(prof["bands"]) == 3  # the OFF filter is skipped
    assert prof["bands"][0] == {"freq": 105, "gain": 5.5, "q": 0.70, "filter": "low-shelf"}
    assert prof["bands"][2]["filter"] == "high-shelf"


def test_parse_rejects_empty_profile():
    with pytest.raises(ValueError):
        eqdb.parse("Preamp: 0.0 dB\n")


def test_render_roundtrip():
    prof = eqdb.parse(SAMPLE)
    again = eqdb.parse(eqdb.render(prof["preamp"], prof["bands"]))
    assert again["preamp"] == prof["preamp"]
    assert again["bands"] == prof["bands"]


def test_search_ranking():
    entries = [
        {"name": "Sennheiser HD 600", "path": "a/b/HD 600", "source": "oratory1990", "rig": ""},
        {"name": "Sennheiser HD 650", "path": "a/b/HD 650", "source": "crinacle", "rig": "711"},
        {"name": "Moondrop Aria", "path": "a/b/Aria", "source": "crinacle", "rig": "711"},
    ]
    hits = eqdb.search("hd 600", entries)
    assert [h["name"] for h in hits] == ["Sennheiser HD 600"]
    assert len(eqdb.search("sennheiser", entries)) == 2
    assert eqdb.search("", entries) == []
