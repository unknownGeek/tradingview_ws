import asyncio
import edge_tts
import os

input_text_file_name = "/Users/h0k00sn/IdeaProjects/tradingview_ws/fx/voiceovers/love_story.txt"

audio_file = "/Users/h0k00sn/IdeaProjects/tradingview_ws/fx/voiceovers/voices/natural_voiceover_2.mp3"

with open(input_text_file_name, "r") as f:
    text = f.read()


async def main():
    # Define the SSML content as a string
    ssml_content = """<speak>
    <prosody rate="medium" pitch="+0st">
        बारिश की पहली बूँदों में भीगती सड़क पर वह लड़की खड़ी थी।
        उसकी आँखों में चमक और हाथों में किताबों का बंडल था।
    </prosody>

    <prosody rate="medium" pitch="+2st">
        आरव, जो कैफ़े में बैठा था, बाहर आया।
        उसने पहली ही नजर में उसे देखा और दिल की धड़कन तेज़ हो गई।
    </prosody>

    <break time="400ms"/>

    <prosody rate="medium" pitch="+1st">
        उसकी मुस्कान में कुछ ऐसा था जो आरव को खींच रहा था।
        "क्या आप भी बारिश का मज़ा ले रही हैं?"
    </prosody>

    <break time="500ms"/>

    <prosody rate="medium" pitch="+0st">
        लड़की ने चौंक कर देखा और हँसते हुए कहा,
        "हाँ, लेकिन किताबों के साथ भीगना आसान नहीं है।"
    </prosody>

    <break time="300ms"/>

    <prosody rate="medium" pitch="+0st">
        आरव ने उसकी किताबों को पकड़ने में मदद की और दोनों कैफ़े में दाखिल हुए।
        बाहर की बारिश और अंदर की गर्म चाय का माहौल, दोनों के बीच की दूरी को कम कर रहा था।
    </prosody>

    <break time="400ms"/>

    <prosody rate="medium" pitch="+1st">
        समय धीरे-धीरे गुजरता रहा, और रोशनी की धीमी चमक दोनों के दिलों में नई उमंग भर रही थी।
        आरव ने साहस करके कहा, "क्या मैं आपको कल फिर मिल सकता हूँ?"
    </prosody>

    <break time="300ms"/>

    <prosody rate="medium" pitch="+0st">
        लड़की ने आँखों में चमक के साथ हाँ कर दिया।
        अगली मुलाकातें, लंबी बातें, और छोटे-छोटे मज़ेदार पल धीरे-धीरे प्यार में बदल गए।
    </prosody>

    <break time="500ms"/>

    <prosody rate="medium" pitch="-2st">
        कुछ दिनों बाद, लड़की अचानक उदास हो गई।
        आरव ने पूछा, "क्या हुआ?"
        लड़की ने धीरे-धीरे कहा, "मुझे डर है कि ये सब सिर्फ एक सपने जैसा है।"
    </prosody>

    <break time="400ms"/>

    <prosody rate="medium" pitch="+2st">
        आरव ने उसके हाथ थामे, "सपने तो सच भी बन सकते हैं, जब हम साथ हों।"
    </prosody>

    <break time="500ms"/>

    <prosody rate="medium" pitch="+0st">
        समाप्त होते हुए कहानी का संदेश यह था कि प्यार कभी योजना से नहीं आता।
        यह छोटे-छोटे पल, एक मुस्कान, एक एहसास और थोड़ी बारिश की आहट से जन्म लेता है।
        और पहली बारिश का वह दिन, दोनों के लिए हमेशा यादगार बन गया।
    </prosody>
    </speak>
    """

    voice = "hi-IN-SwaraNeural"

    communicate = edge_tts.Communicate(ssml_content, voice)
    await communicate.save(audio_file)

    print(f"Audio generated as {audio_file}")
    os.system(f"open {audio_file}")  # macOS

if __name__ == "__main__":
    asyncio.run(main())