import time

from hermes_minitoo import display as D


class _Stub:
    _indicator = D.MiniTooDisplay._indicator


def _ind(tmp_path, monkeypatch, text):
    f = tmp_path / "ind"
    if text is not None:
        f.write_text(text)
    monkeypatch.setattr(D, "INDICATOR_FILE", str(f))
    return _Stub()._indicator()


def test_no_file(tmp_path, monkeypatch):
    assert _ind(tmp_path, monkeypatch, None) is None


def test_rec_is_red(tmp_path, monkeypatch):
    assert _ind(tmp_path, monkeypatch, f"rec:{time.time()}") == D.REC_COLOR


def test_stale_rec_expires(tmp_path, monkeypatch):
    assert _ind(tmp_path, monkeypatch, f"rec:{time.time() - D.REC_MAX_SECONDS - 1}") is None


def test_done_is_green_then_expires(tmp_path, monkeypatch):
    assert _ind(tmp_path, monkeypatch, f"done:{time.time()}") == D.DONE_COLOR
    assert _ind(tmp_path, monkeypatch, f"done:{time.time() - D.DONE_SECONDS - 1}") is None


def test_garbage(tmp_path, monkeypatch):
    assert _ind(tmp_path, monkeypatch, "rec") is None
    assert _ind(tmp_path, monkeypatch, "wat:abc") is None
