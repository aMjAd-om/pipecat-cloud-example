"""PCM energy counters only: no audio retained and no speech-content inference."""
import audioop


class OutputAudioActivity:
    def __init__(self, rms_min):
        self.rms_min = rms_min
        self.reset()

    def reset(self):
        self.duration = self.leading_silence = self.trailing_silence = 0.0
        self.first_nonsilent_at = self.last_nonsilent_end = None

    def observe(self, pcm, sent, end):
        duration = len(pcm) / 32000  # Transport is 16 kHz, mono, PCM16.
        self.duration += duration
        nonsilent = audioop.rms(pcm, 2) >= self.rms_min
        if nonsilent:
            if self.first_nonsilent_at is None:
                self.first_nonsilent_at = sent
            self.last_nonsilent_end = end
            self.trailing_silence = 0.0
        else:
            self.trailing_silence += duration
            if self.first_nonsilent_at is None:
                self.leading_silence += duration
        return nonsilent
