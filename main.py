import asyncio
import base64
import json
import os
import numpy as np
import sounddevice as sd
import scipy.signal
from websockets.client import connect
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class SimpleGeminiVoice:
    def __init__(self):
        self.audio_queue = asyncio.Queue()
        self.api_key = os.environ.get("GEMINI_API_KEY")
        self.model = "gemini-2.0-flash-exp"
        self.uri = f"wss://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:streamGenerateContent?key={API_KEY}"
        self.CHANNELS = 1
        self.RATE = 16000         # Same rate for input/output
        self.CHUNK = 1024         # Larger chunk for smoother streaming
        self.model_speaking = False
        self.pitch_factor = 1.5   # >1.0 raises pitch slightly

    async def start(self):
        self.ws = await connect(
            self.uri,
            extra_headers={"Content-Type": "application/json"}
        )
        await self.ws.send(json.dumps({"setup": {"model": f"models/{self.model}"}}))
        await self.ws.recv()
        print("Connected to Gemini, You can start talking now")

        # --- Send initial prompt to introduce the AI ---
        initial_prompt = (
            "your name is Rev and not any else name and you are trained by revolt motors. "
            "You are an AI assistant of Revolt Motors. "
            "Please respond as Rev would in a helpful and friendly manner and only talk about revolt motors."
        )
        await self.ws.send(json.dumps({
            "realtime_input": {
                "text": initial_prompt
            }
        }))

        async with asyncio.TaskGroup() as tg:
            tg.create_task(self.capture_audio())
            tg.create_task(self.stream_audio())
            tg.create_task(self.play_response())

    async def capture_audio(self):
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time, status):
            if not self.model_speaking:
                data = indata.tobytes()
                asyncio.run_coroutine_threadsafe(
                    self.ws.send(json.dumps({
                        "realtime_input": {
                            "media_chunks": [{"data": base64.b64encode(data).decode(), "mime_type": "audio/pcm"}]
                        }
                    })),
                    loop
                )

        with sd.InputStream(samplerate=self.RATE, channels=self.CHANNELS,
                            blocksize=self.CHUNK, dtype='int16', callback=callback):
            await asyncio.Event().wait()

    async def stream_audio(self):
        async for msg in self.ws:
            response = json.loads(msg)
            try:
                audio_data = response["serverContent"]["modelTurn"]["parts"][0]["inlineData"]["data"]
                if not self.model_speaking:
                    self.model_speaking = True
                    print("\nModel started speaking")
                await self.audio_queue.put(base64.b64decode(audio_data))
            except KeyError:
                pass

            turn_complete = response.get("serverContent", {}).get("turnComplete")
            if turn_complete:
                print("\nEnd of turn")
                await asyncio.sleep(0.5)
                while not self.audio_queue.empty():
                    self.audio_queue.get_nowait()
                self.model_speaking = False
                print("Ready for next input")

    async def play_response(self):
        buffer = np.zeros((0, 1), dtype='float32')

        def callback(outdata, frames, time, status):
            nonlocal buffer
            if len(buffer) >= frames:
                outdata[:] = buffer[:frames]
                buffer = buffer[frames:]
            else:
                outdata[:len(buffer)] = buffer
                outdata[len(buffer):] = 0
                buffer = np.zeros((0, 1), dtype='float32')

        with sd.OutputStream(samplerate=self.RATE, channels=self.CHANNELS,
                             blocksize=self.CHUNK, dtype='float32', callback=callback):
            while True:
                data = await self.audio_queue.get()
                audio_np = np.frombuffer(data, dtype='int16').astype(np.float32) / 32768.0
                audio_np = audio_np.reshape(-1, 1)

                # Normalize audio
                max_val = np.max(np.abs(audio_np))
                if max_val > 0:
                    audio_np /= max_val * 1.1

                # Optional: High-pass filter to reduce bass
                b, a = scipy.signal.butter(1, 300/(self.RATE/2), btype='high')  # 300Hz cutoff
                audio_np = scipy.signal.lfilter(b, a, audio_np, axis=0)

                # Slight pitch adjustment
                num_samples = int(len(audio_np) / self.pitch_factor)
                audio_np = scipy.signal.resample(audio_np, num_samples).reshape(-1, 1)

                buffer = np.vstack((buffer, audio_np))


if __name__ == "__main__":
    client = SimpleGeminiVoice()
    asyncio.run(client.start())
