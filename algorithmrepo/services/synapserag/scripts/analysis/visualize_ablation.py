"""
可视化消融实验结果
生成Recall@K曲线、消融柱状图、权重分布等图表
"""

import sys
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.experiments.exp_configs import ABLATION_CONFIGS, DATASETS

# 设置中文字体和样式
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Arial', 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False
sns.set_style('whitegrid')

# 配置颜色方案
COLOR_MAP = {
    'Baseline': '#808080',          # 灰色
    'MROAW-Prob': '#FF6B6B',        # 红色（您的创新点1）
    'Agentic': '#4ECDC4',           # 青色（您的创新点2）
    'MROAW-Heur': '#FFA07A',        # 橙色
    'Heur+Agentic': '#9B59B6',      # 紫色
    'Full (Ours)': '#2ECC71'        # 绿色（完整方案，突出显示）
}


def load_latest_results(result_dir='experiments/ablation_mroaw_agentic/aggregated'):
    """加载最新的聚合结果"""
    result_path = Path(result_dir)
    latest_file = result_path / 'ablation_results_full_latest.csv'

    if not latest_file.exists():
        print(f"❌ 未找到结果文件: {latest_file}")
        print("   请先运行: python scripts/analysis/aggregate_results.py")
        return None

    df = pd.read_csv(latest_file)
    print(f"✅ 加载结果: {latest_file} ({len(df)} 行)")
    return df


def plot_recall_curves(df, output_dir):
    """
    绘制Recall@K曲线（6配置 × 3数据集 = 3个子图）
    """
    k_values = [1, 2, 5, 10, 20, 30, 50]
    recall_columns = [f'recall@{k}' for k in k_values]

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    fig.suptitle('Recall@K Curves Across Datasets', fontsize=16, fontweight='bold')

    for idx, dataset in enumerate(DATASETS):
        ax = axes[idx]
        dataset_df = df[df['dataset'] == dataset]

        for _, row in dataset_df.iterrows():
            config_short = row['config_short']
            recalls = [row[col] for col in recall_columns]

            ax.plot(
                k_values,
                recalls,
                marker='o',
                label=config_short,
                color=COLOR_MAP.get(config_short, '#000000'),
                linewidth=2.5 if config_short == 'Full (Ours)' else 2,
                markersize=8 if config_short == 'Full (Ours)' else 6
            )

        ax.set_xlabel('K', fontsize=12)
        ax.set_ylabel('Recall@K', fontsize=12)
        ax.set_title(f'{dataset}', fontsize=14, fontweight='bold')
        ax.legend(loc='lower right', fontsize=9)
        ax.grid(True, alpha=0.3)
        ax.set_xscale('log')
        ax.set_xticks(k_values)
        ax.set_xticklabels([str(k) for k in k_values])

    plt.tight_layout()

    output_file = output_dir / f'recall_curves_{datetime.now().strftime("%Y%m%d")}.pdf'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()

    print(f"  ✅ Recall曲线: {output_file}")


def plot_ablation_barplot(df, output_dir):
    """
    绘制消融分析柱状图（展示各组件贡献）
    """
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    fig.suptitle('Ablation Study: Recall@10 Comparison', fontsize=16, fontweight='bold')

    for idx, dataset in enumerate(DATASETS):
        ax = axes[idx]
        dataset_df = df[df['dataset'] == dataset].copy()

        # 按配置顺序排序
        config_order = [ABLATION_CONFIGS[c]['short_name'] for c in ABLATION_CONFIGS.keys()]
        dataset_df['config_short'] = pd.Categorical(
            dataset_df['config_short'],
            categories=config_order,
            ordered=True
        )
        dataset_df = dataset_df.sort_values('config_short')

        # 绘制柱状图
        colors = [COLOR_MAP.get(c, '#000000') for c in dataset_df['config_short']]

        bars = ax.bar(
            range(len(dataset_df)),
            dataset_df['recall@10'],
            color=colors,
            edgecolor='black',
            linewidth=1.5,
            alpha=0.85
        )

        # 突出显示Full (Ours)
        for i, config in enumerate(dataset_df['config_short']):
            if config == 'Full (Ours)':
                bars[i].set_linewidth(3)
                bars[i].set_edgecolor('darkgreen')

        ax.set_xlabel('Configuration', fontsize=12)
        ax.set_ylabel('Recall@10', fontsize=12)
        ax.set_title(f'{dataset}', fontsize=14, fontweight='bold')
        ax.set_xticks(range(len(dataset_df)))
        ax.set_xticklabels(dataset_df['config_short'], rotation=45, ha='right', fontsize=10)
        ax.grid(axis='y', alpha=0.3)

        # 添加数值标签
        for i, (idx_val, row) in enumerate(dataset_df.iterrows()):
            ax.text(
                i,
                row['recall@10'] + 0.01,
                f"{row['recall@10']:.3f}",
                ha='center',
                va='bottom',
                fontsize=9,
                fontweight='bold' if row['config_short'] == 'Full (Ours)' else 'normal'
            )

    plt.tight_layout()

    output_file = output_dir / f'ablation_barplot_{datetime.now().strftime("%Y%m%d")}.pdf'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()

    print(f"  ✅ 消融柱状图: {output_file}")


def plot_improvement_heatmap(df, output_dir):
    """
    绘制相对baseline的提升热力图
    """
    improvements = []

    for dataset in DATASETS:
        dataset_df = df[df['dataset'] == dataset]
        baseline_recall = dataset_df[dataset_df['config'] == 'baseline']['recall@10'].values

        if len(baseline_recall) == 0:
            continue

        baseline_recall = baseline_recall[0]

        for _, row in dataset_df.iterrows():
            if row['config'] == 'baseline':
                continue

            improvement = (row['recall@10'] - baseline_recall) / baseline_recall * 100
            improvements.append({
                'dataset': dataset,
                'config': row['config_short'],
                'improvement': improvement
            })

    if not improvements:
        print("  ⚠️  无法生成提升热力图（缺少baseline结果）")
        return

    improvement_df = pd.DataFrame(improvements)
    pivot = improvement_df.pivot(index='config', columns='dataset', values='improvement')

    # 按配置顺序排序
    config_order = [ABLATION_CONFIGS[c]['short_name'] for c in ABLATION_CONFIGS.keys() if c != 'baseline']
    pivot = pivot.reindex(config_order)

    plt.figure(figsize=(10, 6))
    sns.heatmap(
        pivot,
        annot=True,
        fmt='.1f',
        cmap='RdYlGn',
        center=0,
        cbar_kws={'label': 'Improvement (%)'},
        linewidths=1,
        linecolor='gray'
    )
    plt.title('Improvement over Baseline (Recall@10)', fontsize=14, fontweight='bold')
    plt.xlabel('Dataset', fontsize=12)
    plt.ylabel('Configuration', fontsize=12)
    plt.tight_layout()

    output_file = output_dir / f'improvement_heatmap_{datetime.now().strftime("%Y%m%d")}.pdf'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()

    print(f"  ✅ 提升热力图: {output_file}")


def plot_radar_chart(df, output_dir):
    """
    绘制雷达图（不同数据集的性能）
    """
    # 选择关键配置
    key_configs = ['Baseline', 'MROAW-Prob', 'Agentic', 'Full (Ours)']

    fig, axes = plt.subplots(2, 2, figsize=(14, 14), subplot_kw=dict(projection='polar'))
    axes = axes.flatten()

    for idx, config_short in enumerate(key_configs):
        ax = axes[idx]
        config_df = df[df['config_short'] == config_short]

        # 提取各数据集的Recall@10
        values = []
        for dataset in DATASETS:
            dataset_row = config_df[config_df['dataset'] == dataset]
            if len(dataset_row) > 0:
                values.append(dataset_row['recall@10'].values[0])
            else:
                values.append(0)

        # 闭合雷达图
        values += values[:1]
        angles = np.linspace(0, 2 * np.pi, len(DATASETS), endpoint=False).tolist()
        angles += angles[:1]

        ax.plot(angles, values, 'o-', linewidth=2, color=COLOR_MAP.get(config_short, '#000000'))
        ax.fill(angles, values, alpha=0.25, color=COLOR_MAP.get(config_short, '#000000'))
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(DATASETS, fontsize=10)
        ax.set_ylim(0, max(values) * 1.1)
        ax.set_title(config_short, fontsize=12, fontweight='bold', pad=20)
        ax.grid(True)

    plt.tight_layout()

    output_file = output_dir / f'radar_datasets_{datetime.now().strftime("%Y%m%d")}.pdf'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()

    print(f"  ✅ 雷达图: {output_file}")


def plot_time_comparison(df, output_dir):
    """
    绘制时间开销对比（如果有时间数据）
    """
    if 'time_retrieval' not in df.columns or df['time_retrieval'].isna().all():
        print("  ⚠️  无时间数据，跳过时间对比图")
        return

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    fig.suptitle('Retrieval Time Comparison', fontsize=16, fontweight='bold')

    for idx, dataset in enumerate(DATASETS):
        ax = axes[idx]
        dataset_df = df[df['dataset'] == dataset].copy()

        # 按配置顺序排序
        config_order = [ABLATION_CONFIGS[c]['short_name'] for c in ABLATION_CONFIGS.keys()]
        dataset_df['config_short'] = pd.Categorical(
            dataset_df['config_short'],
            categories=config_order,
            ordered=True
        )
        dataset_df = dataset_df.sort_values('config_short')

        colors = [COLOR_MAP.get(c, '#000000') for c in dataset_df['config_short']]

        ax.bar(
            range(len(dataset_df)),
            dataset_df['time_retrieval'],
            color=colors,
            edgecolor='black',
            alpha=0.85
        )

        ax.set_xlabel('Configuration', fontsize=12)
        ax.set_ylabel('Time (seconds)', fontsize=12)
        ax.set_title(f'{dataset}', fontsize=14, fontweight='bold')
        ax.set_xticks(range(len(dataset_df)))
        ax.set_xticklabels(dataset_df['config_short'], rotation=45, ha='right', fontsize=10)
        ax.grid(axis='y', alpha=0.3)

    plt.tight_layout()

    output_file = output_dir / f'time_comparison_{datetime.now().strftime("%Y%m%d")}.pdf'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()

    print(f"  ✅ 时间对比图: {output_file}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='可视化消融实验结果')
    parser.add_argument(
        '--result-dir',
        default='experiments/ablation_mroaw_agentic/aggregated',
        help='聚合结果目录'
    )
    parser.add_argument(
        '--output-dir',
        default='experiments/ablation_mroaw_agentic/figures',
        help='图表输出目录'
    )

    args = parser.parse_args()

    print("\n" + "="*100)
    print("📊 可视化消融实验结果")
    print("="*100 + "\n")

    # 创建输出目录
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 加载结果
    print("📂 加载数据...")
    df = load_latest_results(args.result_dir)

    if df is None:
        sys.exit(1)

    print("\n🎨 生成可视化...")

    # 生成各类图表
    plot_recall_curves(df, output_dir)
    plot_ablation_barplot(df, output_dir)
    plot_improvement_heatmap(df, output_dir)
    plot_radar_chart(df, output_dir)
    plot_time_comparison(df, output_dir)

    print("\n✅ 可视化完成！")
    print(f"   输出目录: {output_dir}\n")


if __name__ == '__main__':
    main()
