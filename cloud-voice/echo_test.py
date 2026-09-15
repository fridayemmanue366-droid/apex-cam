"""Isolation test: does loading a real ONNX model with onnxruntime-gpu inside
the WebSocket handler work? Logs every step explicitly to modal's own log
stream so the truth is visible even if the client-side close/error relay is
what's broken."""
import modal

app = modal.App("apexcam-echo-test")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("fastapi", "numpy<2", "onnxruntime-gpu==1.19.2")
)
MODELS_VOLUME = modal.Volume.from_name("apexcam-voice-models")


@app.cls(image=image, gpu="L4", volumes={"/models": MODELS_VOLUME})
@modal.concurrent(max_inputs=10)
class EchoServer:
    @modal.asgi_app()
    def web(self):
        from fastapi import FastAPI, WebSocket, WebSocketDisconnect

        web_app = FastAPI()

        @web_app.get("/health")
        def health():
            return {"status": "ok"}

        @web_app.websocket("/ws")
        async def echo(ws: WebSocket):
            import asyncio

            await ws.accept()
            print("WS accepted", flush=True)
            try:
                cfg = await ws.receive_json()
                print("got cfg:", cfg, flush=True)

                def load_model():
                    import time

                    import numpy as np
                    import onnxruntime as ort
                    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
                    print("providers available:", ort.get_available_providers(), flush=True)

                    t0 = time.time()
                    content = ort.InferenceSession("/models/content_vec.onnx", providers=providers)
                    print(f"content_vec loaded in {time.time()-t0:.1f}s", flush=True)

                    t0 = time.time()
                    pitch_model = ort.InferenceSession("/models/rmvpe.onnx", providers=providers)
                    print(f"rmvpe loaded in {time.time()-t0:.1f}s", flush=True)
                    pins = pitch_model.get_inputs()
                    print("rmvpe input:", pins[0].name, pins[0].shape, pins[0].type, flush=True)

                    t0 = time.time()
                    audio16 = np.zeros(1600, np.float32)
                    f0 = pitch_model.run(None, {pins[0].name: audio16[None, :].astype(np.float32)})[0]
                    print(f"rmvpe ran OK in {time.time()-t0:.1f}s, output shape {f0.shape}", flush=True)

                    t0 = time.time()
                    sess = ort.InferenceSession("/models/voices/GuraTalkV2.onnx", providers=providers)
                    print(f"GuraTalkV2 loaded in {time.time()-t0:.1f}s, "
                          f"active providers: {sess.get_providers()}", flush=True)
                    return sess

                sess = await asyncio.to_thread(load_model)
                print("model loaded OK, sending ready", flush=True)
                await ws.send_json({"type": "ready", "providers": sess.get_providers()})

                while True:
                    data = await ws.receive_bytes()
                    print(f"got {len(data)} bytes, echoing back", flush=True)
                    await ws.send_bytes(data)
            except WebSocketDisconnect:
                print("client disconnected", flush=True)
            except Exception as exc:
                print(f"HANDLER EXCEPTION: {type(exc).__name__}: {exc}", flush=True)
                try:
                    await ws.close(code=1011, reason="error")
                except Exception as close_exc:
                    print(f"close also failed: {close_exc}", flush=True)

        return web_app
