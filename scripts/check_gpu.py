"""Execute a local Whisper encoder/decoder pass to verify CUDA runtime loading."""
import argparse
import json
import os


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="base")
    args = parser.parse_args()
    os.environ["VIDEO_CONTEXT_WHISPER_MODEL"] = args.model
    os.environ["VIDEO_CONTEXT_WHISPER_DEVICE"] = "cuda"
    os.environ["VIDEO_CONTEXT_WHISPER_COMPUTE"] = "int8_float16"

    import numpy as np
    from video_context.pipeline import speech_model

    model = speech_model()
    segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="fa")
    list(segments)  # Iteration triggers the actual encoder/decoder GPU kernels.
    print(json.dumps({"status": "verified", "device": model.model.device,
                      "model": args.model, "compute_type": "int8_float16"}))


if __name__ == "__main__":
    main()
