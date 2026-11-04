"""One isolated speech pipeline per Asterisk telephone connection."""
import asyncio
import os

from loguru import logger
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.openai.llm import OpenAILLMService

from voice_agent.providers.azure_stt import StreamingAzureSTT
from voice_agent.providers.foundry_llm import FoundryLLMService
from voice_agent.providers.tts import create_tts
from voice_agent.observability.diagnostics import safe_diagnostic
from voice_agent.observability.timing import LatencyProbe, TurnTiming
from voice_agent.voice.language import LanguageLockedResponse


async def main(connection=None, *, stop_event=None, transport=None, runtime=None,
               publish_transcript=None):
    if transport is None:
        raise ValueError("An Asterisk telephone transport is required")
    if stop_event is None:
        stop_event = asyncio.Event()
    language = os.getenv("DEFAULT_LANGUAGE", "ar")
    survey_enabled = (runtime is not None or os.getenv("AGENT_ID") or
                      os.getenv("SURVEY_CONVERSATION_MANAGER", "false").lower() == "true")
    if survey_enabled:
        from voice_agent.agents.runtime import create_runtime
        runtime = runtime or create_runtime()
        language = runtime.manager.language
    timing = TurnTiming()
    speech_options = ({"language": runtime.config.language} if runtime and runtime.config else {})
    stt = StreamingAzureSTT(timing=timing, **speech_options)
    llm = FoundryLLMService(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        base_url=os.environ["AZURE_OPENAI_BASE_URL"],
        model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        params=OpenAILLMService.InputParams(temperature=0.2, max_tokens=150),
    )
    tts_options = ({"language": language, "preferences": runtime.config.voice}
                   if runtime and runtime.config else {})
    tts = create_tts(timing=timing, **tts_options)
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
    survey_processor = None
    pickup_processor = None
    playback_observer = None
    if survey_enabled:
        from voice_agent.voice.survey import SurveyConversationProcessor
        from voice_agent.voice.playback import PlaybackObserver
        playback_observer = PlaybackObserver()
        manager = runtime.manager
        survey_processor = SurveyConversationProcessor(manager, runtime.decision_client(
            llm._client, os.environ["AZURE_OPENAI_DEPLOYMENT"]), timing, stop_event=stop_event)
        from voice_agent.voice.pickup import PickupGreetingProcessor
        pickup_processor = PickupGreetingProcessor(manager, stop_event)
    conversation = ([survey_processor] if survey_processor else
                    [aggregator.user(), LatencyProbe(timing, "request"), llm])
    from voice_agent.voice.transcript import TranscriptObserver
    customer_transcript = ([TranscriptObserver(publish_transcript, "customer")] if publish_transcript else [])
    agent_transcript = ([TranscriptObserver(publish_transcript, "agent")] if publish_transcript else [])
    pipeline = Pipeline([
        transport.input(), LatencyProbe(timing, "input"), stt,
        *customer_transcript,
        *([pickup_processor] if pickup_processor else []),
        *conversation,
        LanguageLockedResponse(language),
        LatencyProbe(timing, "llm"), tts, LatencyProbe(timing, "audio"),
        transport.output(), *agent_transcript,
        *([] if survey_processor else [aggregator.assistant()]),
        *([playback_observer] if playback_observer else []),
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
        if frame.fatal and stop_event:
            stop_event.set()

    greeted = False

    async def greet(*_args):
        nonlocal greeted
        if not greeted:
            greeted = True
            if pickup_processor:
                from voice_agent.voice.pickup import PickupOpeningFrame
                await task.queue_frames([PickupOpeningFrame()])
            elif survey_processor:
                from voice_agent.voice.survey import SurveyOpeningFrame
                await task.queue_frames([SurveyOpeningFrame()])
            else:
                await task.queue_frames([aggregator.user().get_context_frame()])

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
