"""Bounded pause-aware audio chunks; channel buffers never mix."""


class SpeechChunks:
    def __init__(self, max_seconds=5, *, sample_rate=16000, threshold=.003, pause_seconds=.25):
        import numpy as np

        self.rate = sample_rate
        self.maximum = int(max_seconds * sample_rate)
        self.threshold = threshold
        self.pause_frames = max(1, round(pause_seconds / .02))
        self.frame = max(1, int(sample_rate * .02))
        self.buffer = np.array([], dtype=np.float32)

    def feed(self, samples, *, flush=False):
        import numpy as np

        if len(samples):
            self.buffer = np.concatenate((self.buffer, np.asarray(samples, dtype=np.float32)))
        outputs = []
        while self.buffer.size:
            count = self.buffer.size // self.frame
            if count:
                blocks = self.buffer[:count * self.frame].reshape(count, self.frame)
                voiced = np.sqrt(np.mean(blocks**2, axis=1)) >= self.threshold
            else:
                voiced = np.array([], dtype=bool)
            if not voiced.any() and not (self.buffer.size and np.max(np.abs(self.buffer)) >= self.threshold):
                # Keep a small leading silence context; idle audio stays bounded.
                self.buffer = self.buffer[-self.frame * 6:]
                if flush:
                    self.buffer = np.array([], dtype=np.float32)
                break
            cut = None
            if flush:
                cut = self.buffer.size
            elif self.buffer.size >= self.maximum:
                # Prefer a word boundary in the last second before the ceiling.
                ceiling = self.maximum // self.frame
                last_quiet = None
                for index in range(max(0, ceiling - 50), max(0, ceiling - 3)):
                    if not voiced[index:index + 4].any():
                        last_quiet = (index + 2) * self.frame
                cut = last_quiet or self.maximum
            elif len(voiced) >= self.pause_frames and not voiced[-self.pause_frames:].any():
                cut = self.buffer.size
            if cut is None:
                break
            chunk = self.buffer[:cut].copy()
            self.buffer = self.buffer[cut:]
            if np.sqrt(np.mean(chunk**2)) >= self.threshold:
                # Preserve short answers such as "no" through the existing ASR minimum.
                outputs.append(np.pad(chunk, (0, max(0, 4000 - len(chunk)))))
        return outputs
