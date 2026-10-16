"""
聚合实验结果
从outputs/目录收集所有实验JSON，提取关键指标并生成CSV
"""

import json
import os
import sys
from pathlib import Path
from collections import defaultdict
import pandas as pd
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.experiments.exp_configs import ABLATION_CONFIGS, DATASETS, KEY_METRICS


def find_experiment_results(output_dir='outputs'):
    """
    在outputs目录下查找所有实验结果JSON文件

    Returns:
        dict: {(config, dataset): json_file_path}
    """
    results = {}
    output_path = Path(output_dir)

    if not output_path.exists():
        print(f"❌ 输出目录不存在: {output_dir}")
        return results

    # 遍历outputs/<dataset>/<llm>_<embedding>/experiments/目录
    for dataset_dir in output_path.iterdir():
        if not dataset_dir.is_dir():
            continue

        dataset = dataset_dir.name

        # 查找实验结果JSON
        for model_dir in dataset_dir.iterdir():
            if not model_dir.is_dir():
                continue

            exp_dir = model_dir / 'experiments'
            if not exp_dir.exists():
                continue

            # 收集该目录下的所有JSON文件
            for json_file in exp_dir.glob('exp_*.json'):
                # 尝试解析配置名称（从文件名或内容）
                with open(json_file, 'r') as f:
                    data = json.load(f)
                    config_info = data.get('config', {})

                    # 根据配置匹配对应的config_name
                    config_name = identify_config(config_info)
                    if config_name:
                        key = (config_name, dataset)
                        results[key] = json_file
                        print(f"  找到: {config_name:20s} @ {dataset:15s} -> {json_file.name}")

    return results


def identify_config(config_info):
    """
    根据配置信息识别是哪个消融配置

    Args:
        config_info: 实验JSON中的config字段

    Returns:
        config_name: ABLATION_CONFIGS中的key，如'baseline', 'full_fusion'
    """
    edge_weight_mode = config_info.get('edge_weight_mode', 'uniform')
    use_agentic = config_info.get('use_agentic_ppr_reset', False)

    # 根据特征匹配配置
    for config_name, config_def in ABLATION_CONFIGS.items():
        if (config_def['edge_weight_mode'] == edge_weight_mode and
            config_def.get('use_agentic_ppr_reset', False) == use_agentic):
            return config_name

    return None


def extract_metrics(json_file):
    """
    从实验JSON文件中提取关键指标

    Returns:
        dict: 包含所有关键指标的字典
    """
    with open(json_file, 'r') as f:
        data = json.load(f)

    metrics = {}

    # 提取Recall@K
    recall_data = data.get('recall_at_k', {})
    for k in [1, 2, 5, 10, 20, 30, 50, 100]:
        metrics[f'recall@{k}'] = recall_data.get(str(k), None)

    # 提取QA指标
    qa_metrics = data.get('qa_metrics', {})
    metrics['em'] = qa_metrics.get('exact_match', None)
    metrics['f1'] = qa_metrics.get('f1', None)

    # 提取时间统计
    time_stats = data.get('time_statistics', {})
    metrics['time_retrieval'] = time_stats.get('retrieval_avg', None)
    metrics['time_ppr'] = time_stats.get('ppr_avg', None)

    # 提取图统计
    graph_stats = data.get('graph_statistics', {})
    metrics['num_nodes'] = graph_stats.get('num_nodes', None)
    metrics['num_edges'] = graph_stats.get('num_edges', None)
    metrics['avg_edge_weight'] = graph_stats.get('avg_edge_weight', None)

    return metrics


def aggregate_results(results_dict):
    """
    聚合所有实验结果

    Args:
        results_dict: {(config, dataset): json_file}

    Returns:
        pd.DataFrame: 聚合后的结果表
    """
    rows = []

    for (config_name, dataset), json_file in sorted(results_dict.items()):
        config = ABLATION_CONFIGS[config_name]
        metrics = extract_metrics(json_file)

        row = {
            'config': config_name,
            'config_short': config['short_name'],
            'dataset': dataset,
            'description': config['description'],
            **metrics
        }
        rows.append(row)

    df = pd.DataFrame(rows)
    return df


def print_summary_table(df):
    """打印摘要表（主要指标）"""
    print("\n" + "="*100)
    print("📊 实验结果摘要（Recall@10）")
    print("="*100)

    # 透视表：config × dataset
    pivot = df.pivot(index='config_short', columns='dataset', values='recall@10')

    # 按配置顺序排序
    config_order = [ABLATION_CONFIGS[c]['short_name'] for c in ABLATION_CONFIGS.keys()]
    pivot = pivot.reindex(config_order)

    print(pivot.to_string(float_format=lambda x: f'{x:.3f}' if pd.notna(x) else '-'))
    print("="*100 + "\n")


def save_results(df, output_dir='experiments/ablation_mroaw_agentic/aggregated'):
    """保存聚合结果"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 保存完整CSV
    full_csv = output_path / f'ablation_results_full_{timestamp}.csv'
    df.to_csv(full_csv, index=False)
    print(f"✅ 完整结果已保存: {full_csv}")

    # 保存主要指标CSV（用于论文）
    main_metrics = ['config', 'config_short', 'dataset', 'recall@10', 'recall@20', 'em', 'f1']
    main_df = df[main_metrics]
    main_csv = output_path / f'ablation_results_main_{timestamp}.csv'
    main_df.to_csv(main_csv, index=False)
    print(f"✅ 主要指标已保存: {main_csv}")

    # 保存最新符号链接
    latest_full = output_path / 'ablation_results_full_latest.csv'
    latest_main = output_path / 'ablation_results_main_latest.csv'

    if latest_full.exists():
        latest_full.unlink()
    if latest_main.exists():
        latest_main.unlink()

    latest_full.symlink_to(full_csv.name)
    latest_main.symlink_to(main_csv.name)

    print(f"✅ 最新结果链接已更新")

    return full_csv, main_csv


def compute_improvements(df):
    """计算相对baseline的提升"""
    print("\n" + "="*100)
    print("📈 相对Baseline的提升（%）")
    print("="*100)

    for dataset in DATASETS:
        dataset_df = df[df['dataset'] == dataset]

        baseline_recall10 = dataset_df[
            dataset_df['config'] == 'baseline'
        ]['recall@10'].values

        if len(baseline_recall10) == 0:
            print(f"\n⚠️  {dataset}: 缺少baseline结果")
            continue

        baseline_recall10 = baseline_recall10[0]

        print(f"\n{dataset} (Baseline Recall@10 = {baseline_recall10:.3f}):")
        print("-" * 80)

        for _, row in dataset_df.iterrows():
            if row['config'] == 'baseline':
                continue

            recall10 = row['recall@10']
            if pd.notna(recall10) and pd.notna(baseline_recall10) and baseline_recall10 > 0:
                improvement = (recall10 - baseline_recall10) / baseline_recall10 * 100
                print(f"  {row['config_short']:20s}: {recall10:.3f} ({improvement:+.1f}%)")
            else:
                print(f"  {row['config_short']:20s}: {recall10:.3f} (N/A)")

    print("="*100 + "\n")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='聚合实验结果')
    parser.add_argument(
        '--output-dir',
        default='outputs',
        help='实验输出目录，默认: outputs'
    )
    parser.add_argument(
        '--save-dir',
        default='experiments/ablation_mroaw_agentic/aggregated',
        help='保存聚合结果的目录'
    )

    args = parser.parse_args()

    print("\n" + "="*100)
    print("📊 聚合实验结果")
    print("="*100 + "\n")

    # 查找所有实验结果
    print("🔍 查找实验结果...")
    results = find_experiment_results(args.output_dir)

    if not results:
        print("\n❌ 未找到任何实验结果")
        print(f"   请检查目录: {args.output_dir}\n")
        sys.exit(1)

    print(f"\n✅ 找到 {len(results)} 个实验结果\n")

    # 聚合结果
    print("🔄 聚合结果...")
    df = aggregate_results(results)

    # 打印摘要
    print_summary_table(df)

    # 计算提升
    compute_improvements(df)

    # 保存结果
    print("💾 保存结果...")
    save_results(df, args.save_dir)

    print("\n✅ 聚合完成！\n")
    print("下一步操作:")
    print("  1. 生成可视化: python scripts/analysis/visualize_ablation.py")
    print("  2. 生成LaTeX表格: python scripts/analysis/generate_tables.py")
    print("\n")


if __name__ == '__main__':
    main()
