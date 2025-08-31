from gtts import gTTS

# Script for the TikTok-style short video (from earlier response)
script_text = """Hey Guys! My name is Ananya. Are you dreaming of financial freedom but don’t know where to start?
Here are 3 simple steps to kick off your investing journey today!

Step 1: Start Small – Even ₹500 a month counts!
Step 2: Diversify – Don’t put all your money in one basket!
Step 3: Stay Consistent – Wealth grows with time, not luck!

Want the full beginner’s guide?
Tap the link in bio and read our blog now!
"""

# Generate voiceover with female-style voice
tts = gTTS(text=script_text, lang="en", tld="com")

# Save the audio file
audio_file = "/Users/h0k00sn/IdeaProjects/tradingview_ws/fx/voiceovers/voices/blogify_tiktok_voiceover.mp3"
tts.save(audio_file)

audio_file
