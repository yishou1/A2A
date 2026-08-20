from __future__ import annotations

import argparse
import os

import uvicorn


def configure_public_base_url(host: str, port: int) -> None:
    if "GATEWAY_PUBLIC_BASE_URL" in os.environ:
        return
    public_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    os.environ["GATEWAY_PUBLIC_BASE_URL"] = f"http://{public_host}:{port}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AMOS Commander Gateway")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8030)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if args.workers != 1:
        parser.error("Commander Gateway v1 supports exactly one worker")
    configure_public_base_url(args.host, args.port)
    uvicorn.run(
        "commander_gateway.app:app",
        host=args.host,
        port=args.port,
        workers=1,
    )


if __name__ == "__main__":
    main()
