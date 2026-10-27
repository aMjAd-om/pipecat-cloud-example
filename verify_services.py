"""Independent live Azure checks; no transport session and no extra pipeline LLM call."""
import argparse
import asyncio
import os
import sys
import tempfile
import time
import wave
from pathlib import Path

import azure.cognitiveservices.speech as speechsdk
from openai import AsyncOpenAI
from config import load_settings


async def llm_check():
    start = time.monotonic()
    first = None
    chars = 0
    async with AsyncOpenAI(api_key=os.environ["AZURE_OPENAI_API_KEY"],
                           base_url=os.environ["AZURE_OPENAI_BASE_URL"],
                           timeout=30, max_retries=0) as client:
        stream = await client.chat.completions.create(
            model=os.environ["AZURE_OPENAI_DEPLOYMENT"], stream=True,
            messages=[{"role": "system", "content": "Reply briefly in Arabic."},
                      {"role": "user", "content": "مرحبا، عرفني بنفسك"}],
            temperature=0.2, max_tokens=100)
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                first = first or time.monotonic()
                chars += len(chunk.choices[0].delta.content)
    if not chars:
        raise RuntimeError("Empty LLM response")
    print(f"LLM PASS chars={chars} ttft_ms={(first-start)*1000:.1f} "
          f"total_ms={(time.monotonic()-start)*1000:.1f}")


def speech_config():
    return speechsdk.SpeechConfig(subscription=os.environ["AZURE_SPEECH_KEY"],
                                  region=os.environ["AZURE_SPEECH_REGION"])


async def tts_check():
    loop = asyncio.get_running_loop()
    for language, text, default in (("AR", "السلام عليكم", "ar-OM-AyshaNeural"),
                                     ("EN", "Hello, how are you?", "en-US-JennyNeural")):
        config = speech_config()
        config.speech_synthesis_voice_name = os.getenv(f"AZURE_TTS_VOICE_{language}", default)
        config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm)
        synth = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
        first = []
        def audio(event, samples=first):
            if event.result.audio_data:
                at = time.monotonic()
                loop.call_soon_threadsafe(lambda: samples.append(at) if not samples else None)
        synth.synthesizing.connect(audio)
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(asyncio.to_thread(
                lambda: synth.speak_text_async(text).get()), 30)
            if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted or not first:
                raise RuntimeError("TTS canceled or no streaming audio")
            print(f"TTS {language} PASS bytes={len(result.audio_data)} "
                  f"first_audio_ms={(first[0]-start)*1000:.1f}")
        finally:
            await asyncio.to_thread(lambda: synth.stop_speaking_async().get())
            synth.synthesizing.disconnect_all()
        # Release native SDK objects while the Python event loop is still alive.
        del result, synth


async def stt_check(path):
    with wave.open(path, "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
            raise ValueError("STT fixture must be 16 kHz mono 16-bit PCM WAV")
        pcm = wav.readframes(wav.getnframes())
    config = speech_config()
    config.speech_recognition_language = os.getenv("AZURE_STT_LANGUAGE", "ar-OM")
    stream = speechsdk.audio.PushAudioInputStream(speechsdk.audio.AudioStreamFormat(
        samples_per_second=16000, bits_per_sample=16, channels=1))
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=config, audio_config=speechsdk.audio.AudioConfig(stream=stream))
    loop = asyncio.get_running_loop()
    events = asyncio.Queue()
    def receive(kind, event):
        loop.call_soon_threadsafe(events.put_nowait, (kind, event, time.monotonic()))
    recognizer.recognizing.connect(lambda e: receive("partial", e))
    recognizer.recognized.connect(lambda e: receive("final", e))
    recognizer.canceled.connect(lambda e: receive("error", e))
    try:
        await asyncio.wait_for(asyncio.to_thread(
            lambda: recognizer.start_continuous_recognition_async().get()), 20)
        start = time.monotonic()
        # Pace chunks as a live microphone, then supply trailing silence.
        for offset in range(0, len(pcm), 640):
            stream.write(pcm[offset:offset+640])
            await asyncio.sleep(0.02)
        end = time.monotonic()
        for _ in range(100):
            stream.write(bytes(640))
            await asyncio.sleep(0.02)
        partial, final = None, None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            kind, event, at = await asyncio.wait_for(events.get(), deadline-time.monotonic())
            if kind == "error":
                raise RuntimeError("STT recognition canceled")
            if event.result.text:
                print(f"STT {kind} event chars={len(event.result.text)}")
                if kind == "partial":
                    partial = partial or at
                elif event.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                    final = at
                    break
        if not partial or not final:
            raise RuntimeError("Expected both partial and final streaming transcripts")
        print(f"STT PASS first_partial_ms={(partial-start)*1000:.1f} "
              f"final_after_file_end_ms={(final-end)*1000:.1f}")
    finally:
        await asyncio.wait_for(asyncio.to_thread(
            lambda: recognizer.stop_continuous_recognition_async().get()), 10)
        stream.close()


async def speech_roundtrip():
    """Generate a harmless fixture, then stream it through STT without Daily."""
    config = speech_config()
    config.speech_synthesis_voice_name = os.getenv("AZURE_TTS_VOICE_AR", "ar-OM-AyshaNeural")
    config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Raw16Khz16BitMonoPcm)
    synth = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
    try:
        result = await asyncio.wait_for(asyncio.to_thread(lambda: synth.speak_text_async(
            "السلام عليكم كيف حالك اليوم وما اسمك؟").get()), 30)
        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            raise RuntimeError("Synthetic STT fixture synthesis failed")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-arabic.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(result.audio_data)
            print("Streaming a synthetic Arabic fixture into STT; no microphone or Daily used")
            await stt_check(str(path))
    finally:
        await asyncio.to_thread(lambda: synth.stop_speaking_async().get())


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("service", choices=["llm", "tts", "stt", "speech-roundtrip"])
    parser.add_argument("--wav", help="Non-sensitive 16 kHz mono PCM WAV for streaming STT")
    args = parser.parse_args()
    try:
        load_settings(service="stt" if args.service == "speech-roundtrip" else args.service)
    except ValueError as exc:
        print(f"Configuration required: {exc}", file=sys.stderr)
        sys.exit(2)
    if args.service == "stt":
        if not args.wav:
            parser.error("stt requires --wav")
        await stt_check(args.wav)
    elif args.service == "llm":
        await llm_check()
    elif args.service == "speech-roundtrip":
        await speech_roundtrip()
    else:
        await tts_check()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Verification FAIL ({type(exc).__name__}); check .env and connectivity.", file=sys.stderr)
        sys.exit(1)
