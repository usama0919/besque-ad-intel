from dotenv import load_dotenv
load_dotenv()
from src import generate_image_prompt

blueprint = {
    "visual": {
        "layout": "centered product hero",
        "subject": "natural botanical body oil bottle",
        "palette_mood": "warm golden natural tones",
        "text_placement": "minimal, top",
    }
}

path = generate_image_prompt.generate_image(blueprint, "TEST_IMG_001")
print("Draft image saved to:", path)
