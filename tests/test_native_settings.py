import pytest
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("with_stack_under_test", Path(__file__).resolve().parents[1] / "scripts/with_stack.py")
with_stack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(with_stack)


@pytest.mark.parametrize("explicit,native,expected", [(None, None, None), (None, "large-v3-turbo", "large-v3-turbo"),
                                                      ("explicit-model", "native-model", "explicit-model")])
def test_docker_cpu_profile_does_not_override_native_model(tmp_path, monkeypatch, explicit, native, expected):
    monkeypatch.setattr(with_stack, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("POSTGRES_PASSWORD=test\nVIDEO_CONTEXT_WHISPER_MODEL=base\nVIDEO_CONTEXT_WHISPER_DEVICE=cpu\nVIDEO_CONTEXT_WHISPER_COMPUTE=int8\n" +
        ("VIDEO_CONTEXT_NATIVE_WHISPER_MODEL=" + native + "\n" if native else ""))
    # The helper mutates many keys; isolate its entire environment mapping.
    monkeypatch.setattr(with_stack.os, "environ", {})
    if explicit:
        with_stack.os.environ["VIDEO_CONTEXT_WHISPER_MODEL"] = explicit
    with_stack.configure()
    assert with_stack.os.environ.get("VIDEO_CONTEXT_WHISPER_MODEL") == expected
    assert "VIDEO_CONTEXT_WHISPER_DEVICE" not in with_stack.os.environ
    assert "VIDEO_CONTEXT_WHISPER_COMPUTE" not in with_stack.os.environ


def test_native_scratch_can_use_a_different_volume(tmp_path, monkeypatch):
    monkeypatch.setattr(with_stack, "ROOT", tmp_path)
    scratch = str(tmp_path / "separate-volume-worker")
    monkeypatch.setattr(with_stack.os, "environ", {"VIDEO_CONTEXT_NATIVE_DATA": scratch})
    (tmp_path / ".env").write_text("POSTGRES_PASSWORD=test\n")
    with_stack.configure()
    assert with_stack.os.environ["VIDEO_CONTEXT_DATA"] == scratch
    assert with_stack.os.environ["VIDEO_CONTEXT_DATABASE_URL"].endswith("/video")
