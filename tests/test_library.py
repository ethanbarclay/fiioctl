"""The merged profile search behind `eq search`, `eq apply` and the TUI. No network."""

import contextlib
import json
from types import SimpleNamespace

import pytest

from fiioctl import cli, eqdb, library
from fiioctl.cloud import CloudError

OFFICIAL = {"name": "HD 650", "source": "fiio-official", "rig": "", "preamp": -2.0, "id": 7,
            "bands": [{"freq": 100, "gain": 1.0, "q": 0.7, "filter": "peak"}]}
COMMUNITY = {"name": "HD 650", "source": "fiio-community", "rig": "someone", "preamp": -1.0,
             "id": 7, "bands": [{"freq": 200, "gain": 2.0, "q": 0.7, "filter": "peak"}]}
MEASUREMENT = {"id": 714, "productName": "HD 650", "config": "", "source": "Rtings"}
AUTOEQ = {"name": "HD 650", "path": "oratory1990/over-ear/HD%20650",
          "source": "oratory1990", "rig": "over-ear"}
COMPUTED = {"name": "HD 650", "preamp": -3.0, "description": "target: Harman",
            "bands": [{"freq": 300, "gain": 3.0, "q": 0.7, "filter": "peak"}]}
PARSED = {"preamp": -4.0, "bands": [{"freq": 400, "gain": 4.0, "q": 0.7, "filter": "peak"}]}


class FakeCloud:
    CloudError = CloudError
    presets_fail = measurements_fail = False

    @classmethod
    def presets(cls, device_type, query="", official=True, community=True):
        if cls.presets_fail:
            raise CloudError("catalogue unreachable")
        if not query:  # as with the real service, a preset has to be searched for
            return []
        return ([OFFICIAL] if official else []) + ([COMMUNITY] if community else [])

    @classmethod
    def find_measurements(cls, query, limit=25):
        if cls.measurements_fail:
            raise CloudError("FiiO unreachable")
        return [MEASUREMENT]

    @staticmethod
    def auto_eq(measurement_id, bands=10):
        return {**COMPUTED, "bands": COMPUTED["bands"][:bands]}


class FakeEqdb:
    relevance = staticmethod(eqdb.relevance)
    fail = False

    @classmethod
    def search(cls, query, limit=20):
        if cls.fail:
            raise OSError("no route to host")
        return [AUTOEQ]

    @staticmethod
    def profile(entry):
        return PARSED


@pytest.fixture(autouse=True)
def stubbed(monkeypatch):
    monkeypatch.setattr(library, "cloud", FakeCloud)
    monkeypatch.setattr(library, "eqdb", FakeEqdb)
    monkeypatch.setattr(FakeCloud, "presets_fail", False)
    monkeypatch.setattr(FakeCloud, "measurements_fail", False)
    monkeypatch.setattr(FakeEqdb, "fail", False)


def test_search_merges_every_source_trusted_first():
    found, problems = library.search("hd 650", 110)
    assert problems == []
    assert [r["source"] for r in found] == [
        "fiio-official", "autoeq", "fiio-autoeq", "fiio-community"]


def test_a_better_match_beats_a_more_trusted_source(monkeypatch):
    weak = {**OFFICIAL, "name": "Sennheiser HD 650 warm"}
    monkeypatch.setattr(FakeCloud, "presets", classmethod(
        lambda cls, *a, **k: [weak]))
    found, _ = library.search("hd 650", 110)
    assert found[0]["source"] == "autoeq"
    assert found[-1]["source"] == "fiio-official"


def test_a_community_preset_never_outranks_another_source(monkeypatch):
    """Its exact name must not make a stranger's upload the default `eq apply`."""
    weak = {**OFFICIAL, "name": "Sennheiser HD 650 warm"}
    monkeypatch.setattr(FakeCloud, "presets", classmethod(
        lambda cls, *a, **k: [weak, {**COMMUNITY, "name": "hd 650"}]))
    found, _ = library.search("hd 650", 110)
    assert found[-1]["source"] == "fiio-community"
    assert found[0]["source"] != "fiio-community"


def test_presets_need_a_model():
    found, problems = library.search("hd 650", None)
    assert problems == []
    assert {r["source"] for r in found} == {"autoeq", "fiio-autoeq"}


def test_limit_applies_to_the_merged_list():
    found, _ = library.search("hd 650", 110, limit=2)
    assert [r["source"] for r in found] == ["fiio-official", "autoeq"]


@pytest.mark.parametrize("attr, owner, label", [
    ("presets_fail", FakeCloud, "FiiO presets"),
    ("measurements_fail", FakeCloud, "FiiO measurements"),
    ("fail", FakeEqdb, "AutoEq"),
])
def test_one_failing_source_does_not_cost_the_others(monkeypatch, attr, owner, label):
    monkeypatch.setattr(owner, attr, True)
    found, problems = library.search("hd 650", 110)
    assert found and len(problems) == 1 and problems[0].startswith(label)


def test_ids_differ_between_official_and_community_presets():
    ids = [r["id"] for r in library.search("hd 650", 110)[0]]
    assert len(set(ids)) == len(ids)
    assert "fiio-official:7:HD%20650" in ids and "fiio-community:7:HD%20650" in ids


@pytest.mark.parametrize("position", range(4))
def test_every_result_type_resolves_to_a_profile(position):
    result = library.search("hd 650", 110)[0][position]
    expected = {"fiio-official": 100, "autoeq": 400, "fiio-autoeq": 300, "fiio-community": 200}
    library.resolve(result, 10)
    assert result["bands"][0]["freq"] == expected[result["source"]]
    assert isinstance(result["preamp"], float)


@pytest.mark.parametrize("position", range(4))
def test_every_id_finds_its_way_back_to_the_same_profile(position):
    found = library.search("hd 650", 110)[0][position]
    again = library.lookup(found["id"], 110)
    assert again["id"] == found["id"] and again["source"] == found["source"]
    assert library.resolve(again, 10)["bands"][0]["freq"] == \
        library.resolve(found, 10)["bands"][0]["freq"]


@pytest.mark.parametrize("text", ["hd 650", "focal: utopia", "fiio-autoeq:abc", "autoeq:"])
def test_ordinary_queries_are_not_mistaken_for_ids(text):
    assert library.lookup(text, 110) is None


def test_unknown_preset_id_is_an_error():
    with pytest.raises(CloudError, match="no preset"):
        library.lookup("fiio-official:999:HD%20650", 110)


# -- the CLI ------------------------------------------------------------------


@pytest.fixture
def wired(monkeypatch):
    """A connected KA15 that records the profile it was asked to apply."""
    applied = []

    @contextlib.contextmanager
    def connect(_args):
        yield SimpleNamespace(spec=SimpleNamespace(cloud_id=110), band_count=lambda: 10)

    def apply(_dac, prof, _args):
        applied.append(prof)
        return {"applied": len(prof["bands"]), "dropped": [], "preamp": prof["preamp"]}

    monkeypatch.setattr(cli, "connect", connect)
    monkeypatch.setattr(cli, "candidates", lambda: [{"product_string": "FIIO KA15"}])
    monkeypatch.setattr(cli, "_apply_profile", apply)
    return applied


def test_eq_search_lists_every_source(wired, capsys):
    assert cli.main(["eq", "search", "hd 650"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    for source in ("fiio-official", "autoeq", "fiio-autoeq", "fiio-community"):
        assert f"[{source}" in out


def test_eq_search_json_carries_ids_and_no_internals(wired, capsys):
    assert cli.main(["eq", "search", "hd 650", "--json"]) == cli.EXIT_OK
    rows = json.loads(capsys.readouterr().out)
    assert rows[0] == {"index": 0, "id": "fiio-official:7:HD%20650", "name": "HD 650",
                       "source": "fiio-official", "rig": "", "bands": 1}
    assert rows[1]["bands"] is None


def test_eq_search_without_a_device_says_what_it_skipped(wired, monkeypatch, capsys):
    monkeypatch.setattr(cli, "candidates", list)
    assert cli.main(["eq", "search", "hd 650"]) == cli.EXIT_OK
    captured = capsys.readouterr()
    assert "--product" in captured.err and "fiio-official" not in captured.out


def test_product_names_a_model_with_nothing_plugged_in(wired, monkeypatch, capsys):
    monkeypatch.setattr(cli, "candidates", list)
    assert cli.main(["--product", "KA15", "eq", "search", "hd 650"]) == cli.EXIT_OK
    assert "fiio-official" in capsys.readouterr().out


def test_eq_search_warns_on_a_partial_failure(wired, monkeypatch, capsys):
    monkeypatch.setattr(FakeEqdb, "fail", True)
    assert cli.main(["eq", "search", "hd 650"]) == cli.EXIT_OK
    captured = capsys.readouterr()
    assert "warning: AutoEq" in captured.err and "fiio-autoeq" in captured.out


def test_eq_search_reports_a_total_failure(wired, monkeypatch, capsys):
    monkeypatch.setattr(FakeCloud, "presets_fail", True)
    monkeypatch.setattr(FakeCloud, "measurements_fail", True)
    monkeypatch.setattr(FakeEqdb, "fail", True)
    assert cli.main(["eq", "search", "hd 650"]) == cli.EXIT_DATA
    assert "no route to host" in capsys.readouterr().out


@pytest.mark.parametrize("index, source, freq", [
    (0, "fiio-official", 100), (1, "autoeq", 400), (2, "fiio-autoeq", 300),
    (3, "fiio-community", 200),
])
def test_eq_apply_handles_every_result_type(wired, capsys, index, source, freq):
    argv = ["eq", "apply", "hd 650", "--index", str(index), "--json"]
    assert cli.main(argv) == cli.EXIT_OK
    assert wired[0]["bands"][0]["freq"] == freq
    assert json.loads(capsys.readouterr().out)["source"] == source


def test_eq_apply_accepts_an_id(wired, capsys):
    assert cli.main(["eq", "apply", "fiio-autoeq:714"]) == cli.EXIT_OK
    assert wired[0]["bands"][0]["freq"] == 300
    assert "HD 650" in capsys.readouterr().out


@pytest.mark.parametrize("index", ["4", "-1"])
def test_eq_apply_rejects_an_index_the_search_does_not_have(wired, capsys, index):
    assert cli.main(["eq", "apply", "hd 650", f"--index={index}"]) == cli.EXIT_USAGE
    assert not wired


def test_eq_apply_writes_nothing_when_a_profile_cannot_be_fetched(wired, monkeypatch, capsys):
    def offline(_entry):
        raise OSError("no route to host")

    monkeypatch.setattr(FakeEqdb, "profile", staticmethod(offline))
    argv = ["eq", "apply", "hd 650", "--index", "1"]
    assert cli.main(argv) == cli.EXIT_DATA
    assert not wired
