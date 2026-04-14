import os
import structlog
from datetime import datetime

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)

log = structlog.get_logger()


def main():
    mode = os.getenv("TRAINING_CRON", "").lower()
    
    if mode == "true":
        from .train import train_model
        log.info("starting_training_mode")
        train_model()
    else:
        from .inference import run_inference_loop
        log.info("starting_inference_mode")
        run_inference_loop()


if __name__ == "__main__":
    main()
