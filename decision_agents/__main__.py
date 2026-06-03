"""Command-line entry point for local agent development."""

from __future__ import annotations

import json

from pathlib import Path

import click
import uvicorn

from decision_agents.a2a_service import AGENT_DEFINITIONS, build_app
from decision_agents.config import get_settings
from decision_agents.datasets.iran_israel_war import (
    evaluate_threat_ranking,
    load_iran_israel_records,
)
from decision_agents.datasets import SUPPORTED_SOURCE_FORMATS, load_observation_result_from_csv
from decision_agents.evaluation.compliance import evaluate_compliance_jsonl
from decision_agents.schemas import AgentProfile, AgentRequest


@click.group()
def main() -> None:
    """Project 613 decision agent development commands."""


@main.command("show-config")
def show_config() -> None:
    """Print the active local configuration."""
    settings = get_settings()
    click.echo(settings)


@main.command("list-agents")
def list_agents() -> None:
    """List planned agent service names and default ports."""
    settings = get_settings()
    agents = {
        "track_threat_agent": settings.track_threat_port,
        "decision_planning_agent": settings.decision_planning_port,
        "compliance_authorization_agent": settings.compliance_authorization_port,
    }
    for name, port in agents.items():
        click.echo(f"{name}: http://{settings.host}:{port}")


@main.command("serve")
@click.option(
    "--agent",
    "agent_key",
    required=True,
    type=click.Choice(sorted(AGENT_DEFINITIONS.keys())),
    help="Agent service to start.",
)
@click.option("--host", default=None, help="Host to bind.")
@click.option("--port", default=None, type=int, help="Port to bind.")
def serve(agent_key: str, host: str | None, port: int | None) -> None:
    """Start one local A2A-compatible agent service."""
    settings = get_settings()
    definition = AGENT_DEFINITIONS[agent_key]
    effective_host = host or settings.host
    effective_port = port or definition["default_port"]
    app = build_app(agent_key, effective_host, effective_port)
    click.echo(
        f"Starting {definition['agent_name']} on "
        f"http://{effective_host}:{effective_port}"
    )
    uvicorn.run(app, host=effective_host, port=effective_port)


@main.command("convert-dataset")
@click.option(
    "--source",
    "source_format",
    required=True,
    type=click.Choice(sorted(SUPPORTED_SOURCE_FORMATS)),
    help="Source CSV format to normalize.",
)
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Input AIS/ADS-B CSV file.",
)
@click.option(
    "--output",
    "output_path",
    required=False,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Output AgentRequest JSON file. Defaults to stdout.",
)
@click.option("--limit", default=None, type=int, help="Maximum valid rows to convert.")
@click.option("--request-id", default=None, help="Request id for the generated payload.")
def convert_dataset(
    source_format: str,
    input_path: Path,
    output_path: Path | None,
    limit: int | None,
    request_id: str | None,
) -> None:
    """Convert AIS/ADS-B CSV data to a standard AgentRequest JSON payload."""
    result = load_observation_result_from_csv(source_format, input_path, limit)
    payload = AgentRequest(
        request_id=request_id or f"{source_format}-converted",
        agent_profile=AgentProfile(compute_budget="medium"),
        observations=result.observations,
    )
    text = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2)
    if output_path is None:
        click.echo(text)
    else:
        output_path.write_text(text + "\n", encoding="utf-8")

    if result.warnings:
        click.echo(
            f"Converted {len(result.observations)} observation(s), "
            f"skipped {result.skipped_rows} row(s).",
            err=True,
        )
        for warning in result.warnings:
            click.echo(f"warning: {warning}", err=True)


@main.command("evaluate-threat-ranking")
@click.option(
    "--format",
    "source_format",
    required=True,
    type=click.Choice(["iran_israel_war_2026"]),
    help="Threat-ranking dataset format.",
)
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Input waves/incidents CSV file.",
)
@click.option(
    "--output",
    "output_path",
    required=False,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Output report JSON file. Defaults to stdout.",
)
@click.option("--k", default=10, type=int, help="Ranking cutoff for NDCG/MAP.")
@click.option("--limit", default=None, type=int, help="Maximum rows to evaluate.")
def evaluate_threat_ranking_command(
    source_format: str,
    input_path: Path,
    output_path: Path | None,
    k: int,
    limit: int | None,
) -> None:
    """Evaluate threat-ranking quality on a local conflict CSV dataset."""
    if source_format != "iran_israel_war_2026":
        raise click.ClickException(f"Unsupported format: {source_format}")
    records = load_iran_israel_records(input_path, limit=limit)
    report = evaluate_threat_ranking(records, k=k)
    _write_report(report, output_path)


@main.command("evaluate-compliance")
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Input JSONL compliance evaluation cases.",
)
@click.option(
    "--output",
    "output_path",
    required=False,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Output report JSON file. Defaults to stdout.",
)
def evaluate_compliance_command(input_path: Path, output_path: Path | None) -> None:
    """Evaluate compliance decisions and latency on local JSONL cases."""
    report = evaluate_compliance_jsonl(input_path)
    _write_report(report, output_path)


def _write_report(report: dict, output_path: Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if output_path is None:
        click.echo(text)
    else:
        output_path.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
