"""One isolated survey pipeline per browser or telephone connection."""
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

from azure_services import StreamingAzureSTT, FoundryLLMService
from tts_providers import create_tts
from config import load_settings
from diagnostics import safe_diagnostic
from instrumentation import LatencyProbe, TurnTiming
from language_policy import LanguageLockedResponse


async def main(connection, *, stop_event=None, transport=None):
    telephone = transport is not None
    language = os.getenv("DEFAULT_LANGUAGE", "ar")
    timing = TurnTiming()
    transport = transport or SmallWebRTCTransport(connection, TransportParams(
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
    tts = create_tts(timing=timing)
    context = OpenAILLMContext([{"role": "system", "content": (
        "You are a friendly voice assistant. "
        "The session language is fixed to " + language + ". "
        "Always speak ONLY that language, even if the customer speaks another "
        "language or requests switching. Understand their input without mirroring it. "
        "Begin with a brief greeting in the configured language and introduce yourself. "
        "Use one or two short sentences, plain spoken text, no markdown. "
        "Do not repeat a greeting after the first turn."
    )}])
    aggregator = llm.create_context_aggregator(context)
    rtvi = None if telephone else RTVIProcessor(transport=transport)
    survey_processor = None
    if os.getenv("SURVEY_CONVERSATION_MANAGER", "false").lower() == "true":
        from survey import ConversationManager, Questionnaire
        from survey_voice import FoundryDecisionClient, SurveyConversationProcessor
        manager = ConversationManager(
            Questionnaire.load(os.getenv("SURVEY_QUESTIONNAIRE", "questionnaire.customer-satisfaction.ar.json")),
            language=language,
        )
        survey_processor = SurveyConversationProcessor(manager, FoundryDecisionClient(
            llm._client, os.environ["AZURE_OPENAI_DEPLOYMENT"]), timing)
    conversation = ([survey_processor] if survey_processor else
                    [aggregator.user(), LatencyProbe(timing, "request"), llm])
    pipeline = Pipeline([
        transport.input(), *([rtvi] if rtvi else []), LatencyProbe(timing, "input"), stt,
        *conversation,
        LanguageLockedResponse(language),
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
        if rtvi:
            await rtvi.send_error("A voice service failed. Please stop and restart the conversation.")
        elif frame.fatal and stop_event:
            stop_event.set()

    greeted = False

    async def greet(*_args):
        nonlocal greeted
        if rtvi:
            await rtvi.set_bot_ready()
        if not greeted:
            greeted = True
            if survey_processor:
                from survey_voice import SurveyOpeningFrame
                await task.queue_frames([SurveyOpeningFrame()])
            else:
                await task.queue_frames([aggregator.user().get_context_frame()])

    if rtvi:
        rtvi.add_event_handler("on_client_ready", greet)
    else:
        transport.add_event_handler("on_client_connected", greet)

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
            await task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(running), 5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
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
                   "__main__", "bot", "server", "azure_services", "instrumentation",
                   "asterisk_transport", "telephony_server", "tts_providers"
               })


if __name__ == "__main__":
    from server import run
    run()
