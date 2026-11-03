"""Keep direct informational replies separate from the manager-owned reminder."""
import re


def direct_reply(text, question):
    # Model replies sometimes prefix an exact question copy with an interrogative.
    # Remove the whole copy so a dangling 'هل' does not reach speech synthesis.
    text = re.sub(r"(?:هل\s+)?" + re.escape(question), "", text).strip()
    statements = []
    for sentence in re.split(r"(?<=[.!?؟])\s+", text):
        sentence = sentence.strip()
        if sentence and not sentence.endswith(("?", "؟")):
            statements.append(sentence)
    return " ".join(statements).strip()
