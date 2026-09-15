"""
Pluggable Hyperparameter Grid Search Module for SynapseRAG

Usage:
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

import os
import sys
import json
import time
import random
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from collections import defaultdict
import numpy as np

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.synapserag.SynapseRAG import SynapseRAG
from src.synapserag.utils.config_utils import BaseConfig
from src.synapserag.utils.misc_utils import string_to_bool
from src.synapserag.utils.logging_utils import setup_logging
import logging


@dataclass
class ExperimentResult:
    """Single experiment result"""
    params: Dict[str, Any]
    metrics: Dict[str, float]
    query_results: List[Dict]
    runtime: float
    timestamp: str

    def to_dict(self) -> Dict:
        return asdict(self)


class ExperimentCache:
    """Cache for experiment results to avoid re-running"""

    def __init__(self, cache_dir: str):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "cache_index.json"
        self.cache_index = self._load_index()

    def _load_index(self) -> Dict:
        if self.cache_file.exists():
            with open(self.cache_file, 'r') as f:
                return json.load(f)
        return {}

    def _save_index(self):
        with open(self.cache_file, 'w') as f:
            json.dump(self.cache_index, f, indent=2)

    def _hash_params(self, params: Dict) -> str:
        """Generate hash for parameter dict"""
        param_str = json.dumps(params, sort_keys=True)
        return hashlib.md5(param_str.encode()).hexdigest()

    def has_result(self, params: Dict) -> bool:
        param_hash = self._hash_params(params)
        return param_hash in self.cache_index

    def load_result(self, params: Dict) -> Optional[ExperimentResult]:
        param_hash = self._hash_params(params)
        if param_hash not in self.cache_index:
            return None

        result_file = self.cache_dir / self.cache_index[param_hash]
        if not result_file.exists():
            return None

        with open(result_file, 'r') as f:
            data = json.load(f)
        return ExperimentResult(**data)

    def save_result(self, result: ExperimentResult):
        param_hash = self._hash_params(result.params)
        result_file = f"result_{param_hash}.json"

        with open(self.cache_dir / result_file, 'w') as f:
            json.dump(result.to_dict(), f, indent=2)

        self.cache_index[param_hash] = result_file
        self._save_index()


class GridSearchExperiment:
    """
    Grid search experiment manager for SynapseRAG hyperparameters

    Features:
    - Automatic caching to avoid re-running experiments
    - Query sampling for fast validation
    - Multiple sampling strategies
    - Result saving and visualization

    Example:
        exp = GridSearchExperiment(
            base_config={'dataset': 'sample'},
            param_grid={'damping': [0.3, 0.5, 0.7, 0.85]},
            sample_queries=50,
            sample_strategy='first_n'
        )
        results = exp.run()
    """

    def __init__(
        self,
        base_config: Dict[str, Any],
        param_grid: Dict[str, List[Any]],
        metrics: Optional[List[str]] = None,
        sample_queries: Optional[int] = None,
        sample_strategy: str = 'first_n',
        output_dir: Optional[str] = None,
        use_cache: bool = True,
        log_level: str = 'INFO'
    ):
        """
        Initialize grid search experiment

        Args:
            base_config: Base configuration dict (will be merged with param_grid)
            param_grid: Dictionary of parameters to search, e.g., {'damping': [0.3, 0.5, 0.7]}
            metrics: List of metrics to compute, default: ['recall@10', 'recall@50', 'em', 'f1']
            sample_queries: Number of queries to sample (None = use all queries)
            sample_strategy: 'first_n', 'random', or 'stratified'
            output_dir: Directory to save results (default: experiments/results/<timestamp>)
            use_cache: Whether to cache and reuse results
            log_level: Logging level
        """
        self.base_config = base_config
        self.param_grid = param_grid
        self.metrics = metrics or ['recall@10', 'recall@50', 'em', 'f1']
        self.sample_queries = sample_queries
        self.sample_strategy = sample_strategy
        self.use_cache = use_cache
        self.log_level = log_level

        # Setup output directory
        if output_dir is None:
            param_names = '_'.join(param_grid.keys())
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_dir = f"experiments/results/{param_names}_{timestamp}"
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Setup cache
        if use_cache:
            self.cache = ExperimentCache(str(self.output_dir / "cache"))
        else:
            self.cache = None

        # Setup logging
        log_path = setup_logging(
            log_dir=str(self.output_dir / "logs"),
            run_name="grid_search",
            level=log_level
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"GridSearch initialized. Output: {self.output_dir}")
        self.logger.info(f"Logging to: {log_path}")

        # Load dataset
        self._load_dataset()

        # Generate experiment configurations
        self.experiment_configs = self._generate_configs()
        self.logger.info(f"Generated {len(self.experiment_configs)} experiment configurations")

        self.results: List[ExperimentResult] = []

    def _load_dataset(self):
        """Load dataset and prepare queries"""
        dataset_name = self.base_config.get('dataset', 'sample')

        # Load corpus
        corpus_path = f"reproduce/dataset/{dataset_name}_corpus.json"
        with open(corpus_path, 'r') as f:
            self.corpus = json.load(f)
        self.docs = [f"{doc['title']}\n{doc['text']}" for doc in self.corpus]

        # Load queries and gold answers
        query_path = f"reproduce/dataset/{dataset_name}.json"
        with open(query_path, 'r') as f:
            self.samples = json.load(f)

        self.all_queries = [s['question'] for s in self.samples]
        self.gold_answers = self._get_gold_answers(self.samples)

        # Load gold docs for retrieval evaluation
        try:
            self.gold_docs = self._get_gold_docs(self.samples, dataset_name)
        except:
            self.gold_docs = None
            self.logger.warning("Could not load gold docs, retrieval metrics will be skipped")

        # Sample queries if requested
        if self.sample_queries and self.sample_queries < len(self.all_queries):
            self._sample_queries()
        else:
            self.query_indices = list(range(len(self.all_queries)))

        self.logger.info(f"Loaded {len(self.docs)} documents")
        self.logger.info(f"Using {len(self.query_indices)} queries (sampled from {len(self.all_queries)})")

    def _sample_queries(self):
        """Sample queries based on strategy"""
        n = min(self.sample_queries, len(self.all_queries))

        if self.sample_strategy == 'first_n':
            self.query_indices = list(range(n))
        elif self.sample_strategy == 'random':
            random.seed(42)
            self.query_indices = random.sample(range(len(self.all_queries)), n)
        elif self.sample_strategy == 'stratified':
            # TODO: Implement stratified sampling by difficulty
            self.logger.warning("Stratified sampling not implemented, falling back to random")
            random.seed(42)
            self.query_indices = random.sample(range(len(self.all_queries)), n)
        else:
            raise ValueError(f"Unknown sample_strategy: {self.sample_strategy}")

        self.logger.info(f"Sampled {len(self.query_indices)} queries using strategy '{self.sample_strategy}'")

    def _get_gold_answers(self, samples: List) -> List:
        """Extract gold answers from samples"""
        gold_answers = []
        for sample in samples:
            if 'answer' in sample:
                gold_ans = sample['answer']
            elif 'gold_ans' in sample:
                gold_ans = sample['gold_ans']
            elif 'reference' in sample:
                gold_ans = sample['reference']
            else:
                raise ValueError("No gold answer found in sample")

            if isinstance(gold_ans, str):
                gold_ans = [gold_ans]
            gold_ans = set(gold_ans)

            if 'answer_aliases' in sample:
                gold_ans.update(sample['answer_aliases'])

            gold_answers.append(gold_ans)

        return gold_answers

    def _get_gold_docs(self, samples: List, dataset_name: str) -> List:
        """Extract gold documents from samples"""
        gold_docs = []
        for sample in samples:
            if 'supporting_facts' in sample:
                gold_title = set([item[0] for item in sample['supporting_facts']])
                gold_title_and_content_list = [item for item in sample['context'] if item[0] in gold_title]
                if dataset_name.startswith('hotpotqa'):
                    gold_doc = [item[0] + '\n' + ''.join(item[1]) for item in gold_title_and_content_list]
                else:
                    gold_doc = [item[0] + '\n' + ' '.join(item[1]) for item in gold_title_and_content_list]
            elif 'contexts' in sample:
                gold_doc = [item['title'] + '\n' + item['text'] for item in sample['contexts'] if item['is_supporting']]
            elif 'paragraphs' in sample:
                gold_paragraphs = []
                for item in sample['paragraphs']:
                    if 'is_supporting' in item and item['is_supporting'] is False:
                        continue
                    gold_paragraphs.append(item)
                gold_doc = [item['title'] + '\n' + (item['text'] if 'text' in item else item['paragraph_text']) for item in gold_paragraphs]
            else:
                raise ValueError("No gold docs found in sample")

            gold_doc = list(set(gold_doc))
            gold_docs.append(gold_doc)

        return gold_docs

    def _generate_configs(self) -> List[Dict]:
        """Generate all experiment configurations from param_grid"""
        import itertools

        param_names = list(self.param_grid.keys())
        param_values = list(self.param_grid.values())

        configs = []
        for values in itertools.product(*param_values):
            config = dict(zip(param_names, values))
            configs.append(config)

        return configs

    def _create_config(self, params: Dict) -> BaseConfig:
        """Create BaseConfig with given parameters"""
        config_dict = self.base_config.copy()
        config_dict.update(params)

        # Set defaults if not provided
        config_dict.setdefault('llm_base_url', 'https://openrouter.ai/api/v1')
        config_dict.setdefault('llm_name', 'meta-llama/llama-3.3-70b-instruct')
        config_dict.setdefault('embedding_model_name', 'nvidia/NV-Embed-v2')
        config_dict.setdefault('save_dir', str(self.output_dir / "temp"))
        config_dict.setdefault('corpus_len', len(self.corpus))

        # Convert string bools
        for key in ['force_index_from_scratch', 'force_openie_from_scratch', 'force_rebuild_graph']:
            if key in config_dict and isinstance(config_dict[key], str):
                config_dict[key] = string_to_bool(config_dict[key])

        return BaseConfig(**config_dict)

    def _run_single_experiment(self, params: Dict) -> ExperimentResult:
        """Run a single experiment with given parameters"""
        self.logger.info("=" * 80)
        self.logger.info(f"Running experiment with params: {params}")
        self.logger.info("=" * 80)

        start_time = time.time()

        # Create config
        config = self._create_config(params)

        # Initialize SynapseRAG
        synapserag = SynapseRAG(global_config=config)

        # Index documents (only once, reused across experiments if cache enabled)
        synapserag.index(self.docs)

        # Run retrieval on sampled queries
        query_results = []
        queries = [self.all_queries[i] for i in self.query_indices]
        gold_ans = [self.gold_answers[i] for i in self.query_indices]
        gold_doc = [self.gold_docs[i] for i in self.query_indices] if self.gold_docs else None

        self.logger.info(f"Running retrieval on {len(queries)} queries...")

        retrieval_results = synapserag.retrieve(queries)

        # Compute metrics for each query
        for idx, query in enumerate(queries):
            result = {
                'query': query,
                'retrieved_docs': retrieval_results[idx]['retrieved_docs'],
                'gold_answer': list(gold_ans[idx]),
            }

            if gold_doc:
                result['gold_docs'] = gold_doc[idx]

            query_results.append(result)

        # Aggregate metrics
        metrics = self._compute_metrics(query_results, synapserag)

        runtime = time.time() - start_time

        result = ExperimentResult(
            params=params,
            metrics=metrics,
            query_results=query_results,
            runtime=runtime,
            timestamp=datetime.now().isoformat()
        )

        self.logger.info(f"Experiment completed in {runtime:.2f}s")
        self.logger.info(f"Metrics: {metrics}")

        return result

    def _compute_metrics(self, query_results: List[Dict], synapserag: SynapseRAG) -> Dict[str, float]:
        """Compute aggregated metrics"""
        metrics = {}

        # Compute recall@k for retrieval
        if self.gold_docs:
            for k in [1, 2, 5, 10, 20, 50, 100]:
                if k <= len(query_results[0]['retrieved_docs']):
                    recall_sum = 0
                    for qr in query_results:
                        retrieved = set(qr['retrieved_docs'][:k])
                        gold = set(qr['gold_docs'])
                        if gold:
                            recall = len(retrieved & gold) / len(gold)
                            recall_sum += recall
                    metrics[f'recall@{k}'] = recall_sum / len(query_results)

        # Compute QA metrics (EM, F1) - requires running QA
        # For now, we skip QA to speed up grid search
        # You can enable this if needed

        return metrics

    def run(self) -> List[ExperimentResult]:
        """Run all experiments"""
        self.logger.info("=" * 80)
        self.logger.info(f"Starting grid search with {len(self.experiment_configs)} configurations")
        self.logger.info("=" * 80)

        for i, params in enumerate(self.experiment_configs, 1):
            self.logger.info(f"\n[{i}/{len(self.experiment_configs)}] Testing params: {params}")

            # Check cache
            if self.use_cache and self.cache.has_result(params):
                self.logger.info("Loading cached result...")
                result = self.cache.load_result(params)
                self.results.append(result)
                self.logger.info(f"Metrics: {result.metrics}")
                continue

            # Run experiment
            try:
                result = self._run_single_experiment(params)
                self.results.append(result)

                # Save to cache
                if self.use_cache:
                    self.cache.save_result(result)

            except Exception as e:
                self.logger.error(f"Experiment failed: {e}", exc_info=True)
                continue

        self.logger.info("=" * 80)
        self.logger.info("Grid search completed!")
        self.logger.info("=" * 80)

        return self.results

    def save_results(self, filename: Optional[str] = None):
        """Save all results to JSON"""
        if filename is None:
            filename = "all_results.json"

        output_path = self.output_dir / filename

        results_data = {
            'base_config': self.base_config,
            'param_grid': self.param_grid,
            'sample_queries': self.sample_queries,
            'sample_strategy': self.sample_strategy,
            'results': [r.to_dict() for r in self.results]
        }

        with open(output_path, 'w') as f:
            json.dump(results_data, f, indent=2)

        self.logger.info(f"Results saved to {output_path}")

        # Also save summary CSV
        self._save_summary_csv()

    def _save_summary_csv(self):
        """Save summary CSV with params and metrics"""
        import csv

        output_path = self.output_dir / "summary.csv"

        if not self.results:
            return

        # Get all param and metric names
        param_names = list(self.results[0].params.keys())
        metric_names = list(self.results[0].metrics.keys())

        with open(output_path, 'w', newline='') as f:
            writer = csv.writer(f)

            # Header
            writer.writerow(param_names + metric_names + ['runtime'])

            # Data rows
            for result in self.results:
                row = []
                for pname in param_names:
                    row.append(result.params[pname])
                for mname in metric_names:
                    row.append(result.metrics.get(mname, 0))
                row.append(result.runtime)
                writer.writerow(row)

        self.logger.info(f"Summary CSV saved to {output_path}")

    def plot_results(self):
        """Generate visualization plots"""
        try:
            import matplotlib.pyplot as plt
            import pandas as pd
        except ImportError:
            self.logger.warning("matplotlib or pandas not installed, skipping plots")
            return

        if not self.results:
            self.logger.warning("No results to plot")
            return

        plot_dir = self.output_dir / "plots"
        plot_dir.mkdir(exist_ok=True)

        # Convert results to DataFrame
        rows = []
        for result in self.results:
            row = result.params.copy()
            row.update(result.metrics)
            row['runtime'] = result.runtime
            rows.append(row)

        df = pd.DataFrame(rows)

        # Plot 1: Line chart for each metric vs each param
        param_names = list(self.param_grid.keys())
        metric_names = [m for m in self.metrics if m in df.columns]

        for param in param_names:
            for metric in metric_names:
                plt.figure(figsize=(10, 6))
                plt.plot(df[param], df[metric], marker='o', linewidth=2, markersize=8)
                plt.xlabel(param, fontsize=12)
                plt.ylabel(metric, fontsize=12)
                plt.title(f'{metric} vs {param}', fontsize=14)
                plt.grid(True, alpha=0.3)
                plt.tight_layout()

                plot_path = plot_dir / f"{metric}_vs_{param}.png"
                plt.savefig(plot_path, dpi=150)
                plt.close()

                self.logger.info(f"Saved plot: {plot_path}")

        # Plot 2: Bar chart comparing all configs
        if len(self.results) <= 10:  # Only if not too many configs
            fig, axes = plt.subplots(len(metric_names), 1, figsize=(12, 4 * len(metric_names)))
            if len(metric_names) == 1:
                axes = [axes]

            config_labels = [str(r.params) for r in self.results]
            x = range(len(self.results))

            for idx, metric in enumerate(metric_names):
                values = [r.metrics.get(metric, 0) for r in self.results]
                axes[idx].bar(x, values, alpha=0.7)
                axes[idx].set_ylabel(metric, fontsize=12)
                axes[idx].set_xticks(x)
                axes[idx].set_xticklabels(config_labels, rotation=45, ha='right')
                axes[idx].grid(True, alpha=0.3, axis='y')

            plt.tight_layout()
            plot_path = plot_dir / "all_configs_comparison.png"
            plt.savefig(plot_path, dpi=150)
            plt.close()

            self.logger.info(f"Saved plot: {plot_path}")

        self.logger.info(f"All plots saved to {plot_dir}")

    def print_summary(self):
        """Print a summary of results"""
        if not self.results:
            self.logger.info("No results to summarize")
            return

        print("\n" + "=" * 80)
        print("GRID SEARCH SUMMARY")
        print("=" * 80)

        # Find best config for each metric
        metric_names = list(self.results[0].metrics.keys())

        for metric in metric_names:
            best_result = max(self.results, key=lambda r: r.metrics.get(metric, 0))
            print(f"\nBest {metric}: {best_result.metrics[metric]:.4f}")
            print(f"  Params: {best_result.params}")

        # Show all results sorted by first metric
        if metric_names:
            first_metric = metric_names[0]
            print(f"\n\nAll results sorted by {first_metric}:")
            print("-" * 80)

            sorted_results = sorted(self.results, key=lambda r: r.metrics.get(first_metric, 0), reverse=True)

            for i, result in enumerate(sorted_results, 1):
                print(f"{i}. {result.params}")
                for metric in metric_names:
                    print(f"   {metric}: {result.metrics.get(metric, 0):.4f}")
                print(f"   Runtime: {result.runtime:.2f}s")

        print("=" * 80 + "\n")
