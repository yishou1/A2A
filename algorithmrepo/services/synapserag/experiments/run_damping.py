#!/usr/bin/env python3
"""
Quick script to run damping parameter grid search

Usage:
    python experiments/run_damping.py

    # Custom damping values:
    python experiments/run_damping.py --damping 0.3 0.5 0.7 0.85

    # Use different dataset:
    python experiments/run_damping.py --dataset musique

    # Change sample size:
    python experiments/run_damping.py --sample 100
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from experiments.grid_search import GridSearchExperiment


def main():
    parser = argparse.ArgumentParser(description="Run damping parameter grid search")

    # Damping values
    parser.add_argument(
        '--damping',
        nargs='+',
        type=float,
        default=[0.3, 0.5, 0.7, 0.85],
        help='Damping values to test (default: 0.3 0.5 0.7 0.85)'
    )

    # Dataset
    parser.add_argument(
        '--dataset',
        type=str,
        default='sample',
        choices=['sample', 'musique', 'hotpotqa', '2wikimultihopqa'],
        help='Dataset to use (default: sample)'
    )

    # Sample size
    parser.add_argument(
        '--sample',
        type=int,
        default=50,
        help='Number of queries to sample (default: 50, use 0 for all)'
    )

    # Sample strategy
    parser.add_argument(
        '--strategy',
        type=str,
        default='first_n',
        choices=['first_n', 'random', 'stratified'],
        help='Query sampling strategy (default: first_n)'
    )

    # LLM config
    parser.add_argument(
        '--llm_base_url',
        type=str,
        default='https://openrouter.ai/api/v1',
        help='LLM base URL'
    )
    parser.add_argument(
        '--llm_name',
        type=str,
        default='meta-llama/llama-3.3-70b-instruct',
        help='LLM model name'
    )

    # Embedding config
    parser.add_argument(
        '--embedding_name',
        type=str,
        default='nvidia/NV-Embed-v2',
        help='Embedding model name'
    )

    # Other options
    parser.add_argument(
        '--output_dir',
        type=str,
        default=None,
        help='Output directory (default: auto-generated)'
    )
    parser.add_argument(
        '--no_cache',
        action='store_true',
        help='Disable caching'
    )
    parser.add_argument(
        '--log_level',
        type=str,
        default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='Logging level'
    )

    args = parser.parse_args()

    # Print configuration
    print("=" * 80)
    print("DAMPING PARAMETER GRID SEARCH")
    print("=" * 80)
    print(f"Dataset: {args.dataset}")
    print(f"Damping values: {args.damping}")
    print(f"Sample size: {args.sample if args.sample > 0 else 'all queries'}")
    print(f"Sample strategy: {args.strategy}")
    print(f"LLM: {args.llm_name}")
    print(f"Embedding: {args.embedding_name}")
    print(f"Cache enabled: {not args.no_cache}")
    print("=" * 80 + "\n")

    # Create experiment
    exp = GridSearchExperiment(
        base_config={
            'dataset': args.dataset,
            'llm_base_url': args.llm_base_url,
            'llm_name': args.llm_name,
            'embedding_model_name': args.embedding_name,
            'force_index_from_scratch': False,  # Reuse index
            'force_openie_from_scratch': False,  # Reuse OpenIE
            'force_rebuild_graph': False,  # Reuse graph topology
        },
        param_grid={
            'damping': args.damping,
        },
        metrics=['recall@1', 'recall@2', 'recall@5', 'recall@10', 'recall@20', 'recall@50'],
        sample_queries=args.sample if args.sample > 0 else None,
        sample_strategy=args.strategy,
        output_dir=args.output_dir,
        use_cache=not args.no_cache,
        log_level=args.log_level
    )

    # Run experiments
    results = exp.run()

    # Save results
    exp.save_results()

    # Generate plots
    try:
        exp.plot_results()
    except Exception as e:
        print(f"Warning: Could not generate plots: {e}")
        print("Install matplotlib and pandas to enable plotting:")
        print("  pip install matplotlib pandas")

    # Print summary
    exp.print_summary()

    print(f"\nResults saved to: {exp.output_dir}")


if __name__ == '__main__':
    main()
