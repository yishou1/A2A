#!/usr/bin/env python3
"""
Generic script to run multi-parameter grid search

Usage:
    # Single parameter
    python experiments/run_grid_search.py --params damping:0.3,0.5,0.7

    # Multiple parameters
    python experiments/run_grid_search.py --params damping:0.3,0.5,0.7 linking_top_k:5,10,20

    # With custom dataset
    python experiments/run_grid_search.py --params lambda_mix:0.3,0.5,0.7 --dataset musique
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from experiments.grid_search import GridSearchExperiment


def parse_param_spec(param_spec: str):
    """
    Parse parameter specification like 'damping:0.3,0.5,0.7'
    Returns (param_name, list_of_values)
    """
    name, values_str = param_spec.split(':', 1)
    values = []

    for val_str in values_str.split(','):
        val_str = val_str.strip()
        # Try to parse as number
        try:
            if '.' in val_str:
                values.append(float(val_str))
            else:
                values.append(int(val_str))
        except ValueError:
            # Parse as string (for boolean or enum params)
            if val_str.lower() in ('true', 'false'):
                values.append(val_str.lower() == 'true')
            else:
                values.append(val_str)

    return name.strip(), values


def main():
    parser = argparse.ArgumentParser(
        description="Run multi-parameter grid search",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single parameter
  python run_grid_search.py --params damping:0.3,0.5,0.7

  # Multiple parameters
  python run_grid_search.py --params damping:0.5,0.7 linking_top_k:5,10,20

  # With different dataset
  python run_grid_search.py --params lambda_mix:0.3,0.5,0.7 --dataset musique --sample 100
        """
    )

    # Parameter grid
    parser.add_argument(
        '--params',
        nargs='+',
        required=True,
        help='Parameter specifications in format name:val1,val2,val3'
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

    # Parse parameter grid
    param_grid = {}
    for param_spec in args.params:
        name, values = parse_param_spec(param_spec)
        param_grid[name] = values

    # Print configuration
    print("=" * 80)
    print("MULTI-PARAMETER GRID SEARCH")
    print("=" * 80)
    print(f"Dataset: {args.dataset}")
    print(f"Parameter grid:")
    for name, values in param_grid.items():
        print(f"  {name}: {values}")
    print(f"Sample size: {args.sample if args.sample > 0 else 'all queries'}")
    print(f"Sample strategy: {args.strategy}")
    print(f"LLM: {args.llm_name}")
    print(f"Embedding: {args.embedding_name}")
    print(f"Cache enabled: {not args.no_cache}")

    # Calculate total experiments
    total_experiments = 1
    for values in param_grid.values():
        total_experiments *= len(values)
    print(f"Total experiments: {total_experiments}")
    print("=" * 80 + "\n")

    # Create experiment
    exp = GridSearchExperiment(
        base_config={
            'dataset': args.dataset,
            'llm_base_url': args.llm_base_url,
            'llm_name': args.llm_name,
            'embedding_model_name': args.embedding_name,
            'force_index_from_scratch': False,
            'force_openie_from_scratch': False,
            'force_rebuild_graph': False,
        },
        param_grid=param_grid,
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
