
import asyncio
import edge_tts
import os

audio_file = "/Users/h0k00sn/IdeaProjects/tradingview_ws/fx/voiceovers/voices/natural_voiceover.mp3"


# VOICE = "en-IN-NeerjaNeural"
# text = """Hey Guys Are you dreaming of financial freedom, but don’t know where to start.
# Here are 3 simple steps to kick off your investing journey today.
# Step 1: Start Small – Even ₹500 a month counts,
# Step 2: Diversify – Don’t put all your money in one basket,
# Step 3: Stay Consistent – Wealth grows with time not luck.
# Want the full beginner’s guide, Tap the link in bio and read our blog now
# """


VOICE = "hi-IN-SwaraNeural"
# text = """
# हेलो दोस्तों
# क्या आप भी Financial Freedom का सपना देख रहे हो, लेकिन समझ नहीं आ रहा कहाँ से शुरू करें?
# तो चलो, यहाँ हैं 3 आसान स्टेप्स आपकी Investing Journey शुरू करने के लिए –
#
# Step 1: छोटा शुरू करो – सिर्फ ₹500 महीना भी काफ़ी है।
# Step 2: Diversify करो – सारा पैसा एक ही जगह मत लगाओ।
# Step 3: Consistent रहो – पैसा टाइम से बढ़ता है, न कि किस्मत से।
#
# पूरी Beginner’s Guide चाहिए? तो बायो में दिए लिंक पर क्लिक करो और अभी हमारा ब्लॉग पढ़ो
# """


# VOICE = "ne-NP-HemkalaNeural"
# text = """
# हहेलो साथीहरू
# के तपाईँ पनि Financial Freedom को सपना देख्दै हुनुहुन्छ, तर कहाँबाट सुरु गर्ने भन्ने थाहा छैन?
# चिन्ता नलिनुस्, यहाँ छन् ३ सजिला Step तपाईंको Investing Journey सुरु गर्न –
#
# Step 1: सानो बाट सुरु गर्नुहोस् – महिनामा केवल रू.५०० भए पनि धेरै हुन्छ।
# Step 2: Diversify गर्नुहोस् – सारा पैसा एउटै ठाउँमा नगुमाउनुहोस्।
# Step 3: Consistent रहनुहोस् – सम्पत्ति समयसँगै बढ्छ, भाग्यसँग होइन।
#
# पूरा Beginner’s Guide चाहनुहुन्छ? त भने बायोमा दिइएको लिंकमा क्लिक गर्नुहोस् र हाम्रो ब्लग पढ्नुहोस्
# # """

# input_text_file_name = "/Users/h0k00sn/IdeaProjects/tradingview_ws/fx/voiceovers/input_text.txt"
input_text_file_name = "/Users/h0k00sn/IdeaProjects/tradingview_ws/fx/voiceovers/love_story.txt"

async def main():
    with open(input_text_file_name, "r", encoding="utf-8") as f:
        text = f.read()
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(audio_file)
    os.system(f"open {audio_file}")  # macOS


asyncio.run(main())


