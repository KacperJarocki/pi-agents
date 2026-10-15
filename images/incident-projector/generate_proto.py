"""Generates app/events_pb2.py from the shared v2 contract.

    python generate_proto.py [--schemas ../../schemas/events/v2] [--out app]

Needs grpcio-tools (requirements-build.txt), which bundles protoc.
"""

import argparse
import pathlib
import sys

from grpc_tools import protoc

HERE = pathlib.Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schemas", help="default: <repo>/schemas/events/v2")
    parser.add_argument("--out", default=str(HERE / "app"))
    args = parser.parse_args()
    if args.schemas is None:
        args.schemas = str(HERE.parents[1] / "schemas" / "events" / "v2")
    return protoc.main(
        ["protoc", f"--proto_path={args.schemas}", f"--python_out={args.out}", "events.proto"]
    )


if __name__ == "__main__":
    sys.exit(main())
