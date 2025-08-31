from TTS.api import TTS
from pydub import AudioSegment
import os

# Load the multilingual VITS model (supports Hindi)
tts = TTS(model_name="tts_models/multilingual/multi-dataset/vits_hires", progress_bar=True)

# Output directory
output_dir = "/Users/h0k00sn/IdeaProjects/tradingview_ws/fx/voiceovers/voices"
os.makedirs(output_dir, exist_ok=True)

# Example Hindi story lines with emotions
story = [
    {"text": "बारिश की पहली बूँदों में भीगती सड़क पर वह लड़की खड़ी थी।", "emotion": "sad"},
    {"text": "आरव ने उसकी किताबों को संभाला और मुस्कुराया।", "emotion": "happy"},
    {"text": "मुझे डर है कि ये सब सिर्फ एक सपने जैसा है।", "emotion": "sad"},
    {"text": "लेकिन सपने भी सच हो सकते हैं, जब हम साथ हों।", "emotion": "happy"}
]

# Emotion mapping for VITS
emotion_map = {
    "sad": "sad",
    "happy": "happy",
    "neutral": "neutral"
}

# List to hold generated audio files
audio_files = []

for i, line in enumerate(story):
    style = emotion_map.get(line["emotion"], "neutral")
    file_path = os.path.join(output_dir, f"line_{i+1}.mp3")
    tts.tts_to_file(
        text=line["text"],
        speaker="female",
        language="hi",
        style=style,
        file_path=file_path
    )
    print(f"Generated: {file_path}")
    audio_files.append(file_path)

# Combine all lines into a single mp3
combined = AudioSegment.empty()
for file in audio_files:
    audio = AudioSegment.from_file(file)
    combined += audio + AudioSegment.silent(duration=300)  # 300ms pause between lines

final_file = os.path.join(output_dir, "natural_voiceover_emotional_full.mp3")
combined.export(final_file, format="mp3")
print(f"Combined story saved: {final_file}")

# Play the final file (macOS)
os.system(f"open {final_file}")
