"""One isolated Azure pipeline per native SmallWebRTC connection."""
import asyncio
import os
import sys

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.network.small_webrtc import SmallWebRTCTransport
from pipecat.processors.frameworks.rtvi import RTVIProcessor

from azure_services import StreamingAzureSTT, StreamingAzureTTS, FoundryLLMService
from config import load_settings
from diagnostics import safe_diagnostic
from instrumentation import LatencyProbe, TurnTiming


async def main(connection, *, stop_event=None):
    timing = TurnTiming()
    transport = SmallWebRTCTransport(connection, TransportParams(
        audio_in_enabled=True, audio_out_enabled=True,
        audio_in_sample_rate=16000, audio_out_sample_rate=24000,
        vad_analyzer=SileroVADAnalyzer(),
    ))
    stt = StreamingAzureSTT(timing=timing)
    llm = FoundryLLMService(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        base_url=os.environ["AZURE_OPENAI_BASE_URL"],
        model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        params=OpenAILLMService.InputParams(temperature=0.2, max_tokens=150),
    )
    tts = StreamingAzureTTS(timing=timing)
    context = OpenAILLMContext([{"role": "system", "content": (
        "You are a friendly bilingual Arabic and English voice assistant. "
        "Begin with a brief Arabic greeting and introduce yourself. "
        "Reply in Arabic unless the customer speaks or requests English. "
        "Use one or two short sentences, plain spoken text, no markdown. "
        "Do not repeat a greeting after the first turn."
    )}])
    aggregator = llm.create_context_aggregator(context)
    rtvi = RTVIProcessor(transport=transport)
    survey_processor = None
    if os.getenv("SURVEY_CONVERSATION_MANAGER", "false").lower() == "true":
        from survey import ConversationManager, Questionnaire
        from survey_voice import FoundryDecisionClient, SurveyConversationProcessor
        manager = ConversationManager(
            Questionnaire.load(os.getenv("SURVEY_QUESTIONNAIRE", "questionnaire.example.json")),
            language=os.getenv("DEFAULT_LANGUAGE", "ar"),
        )
        survey_processor = SurveyConversationProcessor(manager, FoundryDecisionClient(
            llm._client, os.environ["AZURE_OPENAI_DEPLOYMENT"]), timing)
    conversation = ([survey_processor] if survey_processor else
                    [aggregator.user(), LatencyProbe(timing, "request"), llm])
    pipeline = Pipeline([
        transport.input(), rtvi, LatencyProbe(timing, "input"), stt,
        *conversation,
        LatencyProbe(timing, "llm"), tts, LatencyProbe(timing, "audio"),
        transport.output(), *([] if survey_processor else [aggregator.assistant()]),
    ])
    task = PipelineTask(pipeline, params=PipelineParams(
        audio_in_sample_rate=16000, audio_out_sample_rate=24000,
        allow_interruptions=True, enable_metrics=True, enable_usage_metrics=True,
    ))
    from pipecat.frames.frames import ErrorFrame
    task.set_reached_upstream_filter((ErrorFrame,))

    @task.event_handler("on_frame_reached_upstream")
    async def report_error(_task, frame):
        logger.error("Pipeline service error; fatal={}; {}", frame.fatal,
                     safe_diagnostic(frame.error))
        await rtvi.send_error("A voice service failed. Please stop and restart the conversation.")

    greeted = False

    @rtvi.event_handler("on_client_ready")
    async def greet(_rtvi):
        nonlocal greeted
        await rtvi.set_bot_ready()
        if not greeted:
            greeted = True
            if survey_processor:
                from survey_voice import SurveyOpeningFrame
                await task.queue_frames([SurveyOpeningFrame()])
            else:
                await task.queue_frames([aggregator.user().get_context_frame()])

    @transport.event_handler("on_client_disconnected")
    async def leave(_transport, _connection):
        await task.cancel()

    # Uvicorn owns process signals; individual sessions must not install handlers.
    runner = PipelineRunner(handle_sigint=False)
    running = asyncio.create_task(runner.run(task))
    stopping = asyncio.create_task(stop_event.wait()) if stop_event else None
    try:
        if stopping:
            await asyncio.wait((running, stopping), return_when=asyncio.FIRST_COMPLETED)
            if not running.done():
                # Use Pipecat cancellation before cancelling the coroutine that owns it.
                await task.cancel()
        await running
    finally:
        if survey_processor:
            survey_processor.manager.clear()
        if stopping:
            stopping.cancel()
            await asyncio.gather(stopping, return_exceptions=True)
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        await stt.close()
        await tts.close()
        await llm._client.close()


def configure_logging():
    logger.remove()
    logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO"))
    try:
        load_settings()
    except ValueError as exc:
        logger.error("Configuration required: {}", exc)
        raise
    logger.remove()
    # Third-party debug messages can contain transcripts or request bodies.
    logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO"),
               filter=lambda r: r["name"] in {
                   "__main__", "bot", "server", "azure_services", "instrumentation"
               })


if __name__ == "__main__":
    from server import run
    run()
