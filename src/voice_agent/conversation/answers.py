"""Rating parsing and answer normalization."""
import re


def rating_tokens(text):
    words = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
             "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "صفر": 0, "واحد": 1, "واحدة": 1, "اثنين": 2, "اثنان": 2,
             "ثلاثة": 3, "ثلاث": 3, "أربعة": 4, "اربعة": 4, "اربع": 4,
             "أربع": 4, "خمسة": 5, "خمس": 5, "ستة": 6, "سبعة": 7,
             "ثمانية": 8, "تسعة": 9, "عشرة": 10}
    text = re.sub(r"[\u064b-\u065f\u0670]", "", text.lower())
    tokens = re.findall(r"\d+|[^\W\d_]+", text)
    return [int(t) if t.isdecimal() else words[t] for t in tokens
            if t.isdecimal() or t in words]


def normalize_answer(text):
    text = re.sub(r"[\u064b-\u065f\u0670]", "", text).lower()
    return " ".join(re.findall(r"[^\W_]+", text))
