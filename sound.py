from pysysaudio import SystemAudioRecorder
import time
import whisper
import torch

model = whisper.load_model("base", device="cpu")
recorder = SystemAudioRecorder(sample_rate=48000, channels=2)
recorder.start_recording("output.wav")
time.sleep(10)
output_path = recorder.stop_recording()
print(f"Recording saved to: {output_path}")
print("Transcribing...")
result = model.transcribe(output_path, fp16=False)
print("Transcription:")
print(result["text"])
