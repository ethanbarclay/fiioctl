"""CLI plumbing: error reporting and result shaping. No hardware, no network."""

import contextlib

import pytest

from fiioctl import cli


@contextlib.contextmanager
def _no_device(_args):
    """Stand in for connect(): the tests here never reach the device."""
    yield object()


def test_data_errors_includes_catalogue_failures():
    """main() handles CloudError centrally, so no subcommand needs its own
    try/except. If this ever stops holding, those eight handlers come back."""
    from fiioctl.cloud import CloudError
    assert CloudError in cli.data_errors()
    assert ValueError in cli.data_errors()


def test_catalogue_failure_becomes_exit_data(monkeypatch, capsys):
    from fiioctl.cloud import CloudError

    def boom(_args, _dac):
        raise CloudError("catalogue unreachable")

    monkeypatch.setattr(cli, "connect", _no_device)
    monkeypatch.setattr(cli, "_cloud_presets", boom)

    assert cli.main(["cloud", "list"]) == cli.EXIT_DATA
    assert "catalogue unreachable" in capsys.readouterr().out


def test_catalogue_failure_reports_as_json_when_asked(monkeypatch, capsys):
    import json

    from fiioctl.cloud import CloudError

    def boom(_args, _dac):
        raise CloudError("no catalogue id")

    monkeypatch.setattr(cli, "connect", _no_device)
    monkeypatch.setattr(cli, "_cloud_presets", boom)

    assert cli.main(["--json", "cloud", "list"]) == cli.EXIT_DATA
    assert json.loads(capsys.readouterr().out) == {"error": "no catalogue id"}


def test_bad_input_becomes_exit_data_not_a_traceback(monkeypatch, capsys):
    """build_parser() resolves the handler at call time, so patching the module
    global before main() runs is enough to stand in for it."""
    def boom(_args):
        raise ValueError("not a number")

    monkeypatch.setattr(cli, "cmd_devices", boom)

    assert cli.main(["devices"]) == cli.EXIT_DATA
    assert "not a number" in capsys.readouterr().out


# -- result shaping -----------------------------------------------------------


def test_labelled_adds_a_name_only_when_there_is_one():
    assert cli.labelled("language", 1) == {"language": 1, "label": "english"}
    assert cli.labelled("language", 0) == {"language": 0, "label": "chinese"}
    # volume has no enum, so no label is invented for it
    assert cli.labelled("volume", 30) == {"volume": 30}


def test_labelled_passes_extra_fields_through():
    assert cli.labelled("language", 1, written=True) == {
        "language": 1, "written": True, "label": "english"}


def test_labelled_keeps_the_raw_value_for_machines():
    """A script compares the number; the label is additive, never a replacement."""
    out = cli.labelled("language", 1)
    assert out["language"] == 1 and isinstance(out["language"], int)


def test_shown_prefers_the_name():
    assert cli.shown("language", 1) == "english"
    assert cli.shown("volume", 30) == 30


@pytest.mark.parametrize("before,after,expected", [
    ({"3": "00"}, {"3": "01"}, [(3, "00", "01")]),
    ({"3": "00"}, {}, [(3, "00", None)]),
    ({}, {"3": "00"}, [(3, None, "00")]),
    ({"3": "00"}, {"3": "00"}, []),
])
def test_diff_scans_cases(before, after, expected):
    assert cli.diff_scans(before, after) == expected
