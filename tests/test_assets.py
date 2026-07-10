from src import assets


def test_local_storage_saves_bytes(tmp_path):
    store = assets.LocalStorage(base_dir=tmp_path)
    path = store.save_bytes(b"hello", "test.jpg")
    assert (tmp_path / "test.jpg").read_bytes() == b"hello"


def test_get_storage_defaults_to_local(monkeypatch):
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    assert type(assets.get_storage()).__name__ == "LocalStorage"


def test_gcs_backend_not_provisioned(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "gcs")
    import pytest
    with pytest.raises(NotImplementedError):
        assets.get_storage()
