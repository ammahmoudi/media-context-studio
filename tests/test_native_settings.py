import pytest

from scripts import with_stack


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
