"""
SynapseRAG Hyperparameter Grid Search Experiments

This module provides tools for running hyperparameter grid search experiments
on SynapseRAG with automatic caching, result saving, and visualization.

Quick Start:
    # Run damping parameter search
    python experiments/run_damping.py

    # Run custom parameter search
    python experiments/run_grid_search.py --params damping:0.3,0.5,0.7

Programmatic Usage:
    from experiments.grid_search import GridSearchExperiment

    exp = GridSearchExperiment(
        base_config={'dataset': 'sample'},
        param_grid={'damping': [0.3, 0.5, 0.7, 0.85]},
        sample_queries=50
    )
    results = exp.run()
    exp.save_results()
    exp.plot_results()
"""

from .grid_search import GridSearchExperiment, ExperimentResult, ExperimentCache

__all__ = ['GridSearchExperiment', 'ExperimentResult', 'ExperimentCache']
