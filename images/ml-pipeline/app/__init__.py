from .train import main as train_main
from .inference import run_inference_loop as inference_main

__all__ = ["train_main", "inference_main"]
