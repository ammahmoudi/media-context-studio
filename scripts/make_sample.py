"""Generate a two-slide course sample, optionally with Windows speech synthesis."""
import argparse
import os
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--speech", action="store_true")
    args = parser.parse_args()
    root = Path("data/sample").resolve()
    root.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 48)
    for index, (background, title, body) in enumerate([
        ("#162238", "Graph Algorithms", "Vertices and edges"),
        ("#f5e5bd", "Breadth First Search", "Explore one level at a time")
    ], 1):
        image = Image.new("RGB", (1280, 720), background)
        draw = ImageDraw.Draw(image)
        color = "white" if index == 1 else "black"
        draw.text((80, 200), title, font=font, fill=color)
        draw.text((80, 340), body, font=font, fill=color)
        image.save(root / f"slide{index}.png")
    args_ffmpeg = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-loop", "1", "-t", "5",
                   "-i", str(root / "slide1.png"), "-loop", "1", "-t", "5", "-i", str(root / "slide2.png"),
                   "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]",
                   "-r", "10", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(root / "silent.mp4")]
    subprocess.run(args_ffmpeg, check=True)
    if args.speech:
        script = root / "speak.ps1"
        script.write_text("$ErrorActionPreference = 'Stop'\nAdd-Type -AssemblyName System.Speech\n"
                          "$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
                          "$speaker.SetOutputToWaveFile((Join-Path $PSScriptRoot 'speech.wav'))\n"
                          "$speaker.Speak('Graph algorithms work with vertices and edges. Breadth first search explores one level at a time.')\n"
                          "$speaker.Dispose()\n", encoding="utf-8")
        shell = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        subprocess.run([str(shell), "-NoProfile", "-File", str(script)], check=True)
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(root / "silent.mp4"),
                        "-i", str(root / "speech.wav"), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                        "-c:a", "aac", "-t", "10", str(root / "course.mp4")], check=True)
    print(root / ("course.mp4" if args.speech else "silent.mp4"))


if __name__ == "__main__":
    main()
