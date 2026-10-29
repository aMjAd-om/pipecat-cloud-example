"""Streaming SDK adapters for gaps in the pinned Pipecat 0.0.72 services.

SDK callbacks run on worker threads: marshal every callback onto the asyncio loop.
Recognition remains active during silence and bot speech; Pipecat VAD owns turns.
"""
import asyncio
import os
from xml.sax.saxutils import escape

import azure.cognitiveservices.speech as speechsdk
from loguru import logger
from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame, TTSStartedFrame, TTSStoppedFrame
from pipecat.services.azure.tts import AzureBaseTTSService, sample_rate_to_output_format
from pipecat.transcriptions.language import Language
from voice_agent.observability.diagnostics import speech_cancellation


class StreamingAzureTTS(AzureBaseTTSService):
    """Use native sentence aggregation/SSML, with isolated streaming SDK requests.

    A fresh synthesizer per sentence prevents old callbacks reaching a new turn.
    Finally stops synthesis on cancellation, including Pipecat barge-in.
    """
    def __init__(self, *, timing):
        super().__init__(api_key=os.environ["AZURE_SPEECH_KEY"],
                         region=os.environ["AZURE_SPEECH_REGION"], sample_rate=24000,
                         params=self.InputParams(language=Language.AR))
        self.timing = timing
        self._active = set()
        self._language = os.getenv("DEFAULT_LANGUAGE", "ar")

    async def run_tts(self, text):
        self._voice_id = os.getenv("AZURE_TTS_VOICE_AR", "ar-SA-HamedNeural") \
            if self._language == "ar" else os.getenv("AZURE_TTS_VOICE_EN", "en-US-JennyNeural")
        self._settings["language"] = "-".join(self._voice_id.split("-")[:2])
        config = speechsdk.SpeechConfig(subscription=self._api_key, region=self._region)
        config.set_speech_synthesis_output_format(sample_rate_to_output_format(self.sample_rate))
        synth = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
        self._active.add(synth)
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        accepting = True

        def emit(kind, data=None):
            def enqueue():
                if accepting:
                    queue.put_nowait((kind, data))
            loop.call_soon_threadsafe(enqueue)

        synth.synthesizing.connect(lambda e: emit("audio", bytes(e.result.audio_data))
                                  if e.result.audio_data else None)
        synth.synthesis_completed.connect(lambda e: emit("done"))
        synth.synthesis_canceled.connect(lambda e: emit("error", e))
        self.timing.mark("tts_start", once=True)
        try:
            yield TTSStartedFrame()
            synth.speak_ssml_async(self._construct_ssml(escape(text)))
            while True:
                kind, data = await asyncio.wait_for(queue.get(), 20)
                if kind == "done":
                    break
                if kind == "error":
                    logger.error("Azure TTS canceled: {}", speech_cancellation(
                        getattr(getattr(data, "result", None), "cancellation_details", None)))
                    yield ErrorFrame("Azure TTS request canceled or failed")
                    break
                self.timing.mark("tts_first", once=True)
                yield TTSAudioRawFrame(data, self.sample_rate, 1)
        except asyncio.TimeoutError:
            yield ErrorFrame("Azure TTS audio timeout")
        except Exception:
            yield ErrorFrame("Azure TTS synthesis failed")
        finally:
            accepting = False
            await self._stop_synth(synth)
            self._active.discard(synth)
        yield TTSStoppedFrame()

    async def _stop_synth(self, synth):
        try:
            await asyncio.wait_for(asyncio.to_thread(
                lambda: synth.stop_speaking_async().get()), 5)
        except Exception:
            logger.warning("Azure TTS stop timed out/failed")
        finally:
            for signal in (synth.synthesizing, synth.synthesis_completed, synth.synthesis_canceled):
                signal.disconnect_all()

    async def close(self):
        await asyncio.gather(*(self._stop_synth(s) for s in list(self._active)))
        self._active.clear()

    async def stop(self, frame):
        await self.close()
        await super().stop(frame)

    async def cancel(self, frame):
        await self.close()
        await super().cancel(frame)
