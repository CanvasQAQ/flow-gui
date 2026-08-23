import os

import uvicorn

if os.environ.get("FLOWPILOT_DEMO") == "1":
    from .demo_api import app
else:
    from .api import app


class FlowPilotServer(uvicorn.Server):
    def handle_exit(self, sig: int, frame) -> None:
        # Close the long-lived SSE stream before Uvicorn waits for open HTTP
        # connections, then enter the normal lifespan worker drain.
        app.state.shutdown_requested.set()
        super().handle_exit(sig, frame)


def main() -> None:
    config = uvicorn.Config(
        app, host="127.0.0.1", port=int(os.environ.get("FLOWPILOT_BACKEND_PORT", "8765")),
        timeout_graceful_shutdown=15,
    )
    FlowPilotServer(config).run()


if __name__ == "__main__":
    main()
