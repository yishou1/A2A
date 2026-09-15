"""
生成LaTeX表格
用于论文的主结果表、消融分析表、时间开销表等
"""

import sys
from pathlib import Path
import pandas as pd
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.experiments.exp_configs import ABLATION_CONFIGS, DATASETS


def load_latest_results(result_dir='experiments/ablation_mroaw_agentic/aggregated'):
    """加载最新的聚合结果"""
    result_path = Path(result_dir)
    latest_file = result_path / 'ablation_results_full_latest.csv'

    if not latest_file.exists():
        print(f"❌ 未找到结果文件: {latest_file}")
        return None

    df = pd.read_csv(latest_file)
    print(f"✅ 加载结果: {latest_file} ({len(df)} 行)")
    return df


def generate_main_results_table(df, output_dir):
    """
    生成主结果表（Recall@10, Recall@20, EM, F1）
    """
    latex_lines = []

    latex_lines.append(r"\begin{table}[t]")
    latex_lines.append(r"\centering")
    latex_lines.append(r"\caption{Main Results: Retrieval and QA Performance}")
    latex_lines.append(r"\label{tab:main_results}")
    latex_lines.append(r"\resizebox{\textwidth}{!}{%")
    latex_lines.append(r"\begin{tabular}{l|cc|cc|cc}")
    latex_lines.append(r"\toprule")

    # 表头
    header = r"\multirow{2}{*}{\textbf{Method}} & "
    header += r"\multicolumn{2}{c|}{\textbf{MuSiQue}} & "
    header += r"\multicolumn{2}{c|}{\textbf{2WikiMQA}} & "
    header += r"\multicolumn{2}{c}{\textbf{HotpotQA}} \\"
    latex_lines.append(header)

    latex_lines.append(r"\cmidrule(lr){2-3} \cmidrule(lr){4-5} \cmidrule(lr){6-7}")
    latex_lines.append(r"& R@10 & R@20 & R@10 & R@20 & R@10 & R@20 \\")
    latex_lines.append(r"\midrule")

    # 数据行
    for config_name in ABLATION_CONFIGS.keys():
        config = ABLATION_CONFIGS[config_name]
        method_name = config['short_name']

        # 转义特殊字符
        if '(Ours)' in method_name:
            method_name = r"\textbf{" + method_name.replace('(Ours)', '') + r"} \textit{(Ours)}"

        row_data = [method_name]

        for dataset in DATASETS:
            dataset_df = df[(df['config'] == config_name) & (df['dataset'] == dataset)]

            if len(dataset_df) > 0:
                r10 = dataset_df['recall@10'].values[0]
                r20 = dataset_df['recall@20'].values[0]

                # 格式化（保留3位小数，转为百分比样式）
                r10_str = f"{r10*100:.1f}" if pd.notna(r10) else "-"
                r20_str = f"{r20*100:.1f}" if pd.notna(r20) else "-"

                # 如果是Full (Ours)，加粗最佳结果
                if config_name == 'full_fusion':
                    r10_str = r"\textbf{" + r10_str + "}"
                    r20_str = r"\textbf{" + r20_str + "}"

                row_data.extend([r10_str, r20_str])
            else:
                row_data.extend(["-", "-"])

        latex_lines.append(" & ".join(row_data) + r" \\")

    latex_lines.append(r"\bottomrule")
    latex_lines.append(r"\end{tabular}")
    latex_lines.append(r"}")
    latex_lines.append(r"\end{table}")

    # 保存文件
    output_file = output_dir / 'main_results.tex'
    with open(output_file, 'w') as f:
        f.write('\n'.join(latex_lines))

    print(f"  ✅ 主结果表: {output_file}")


def generate_ablation_table(df, output_dir):
    """
    生成消融分析表（展示各组件贡献）
    """
    latex_lines = []

    latex_lines.append(r"\begin{table}[t]")
    latex_lines.append(r"\centering")
    latex_lines.append(r"\caption{Ablation Study: Component Contribution Analysis (Recall@10)}")
    latex_lines.append(r"\label{tab:ablation}")
    latex_lines.append(r"\begin{tabular}{lc|ccc}")
    latex_lines.append(r"\toprule")
    latex_lines.append(r"\textbf{Method} & \textbf{Components} & \textbf{MuSiQue} & \textbf{2WikiMQA} & \textbf{HotpotQA} \\")
    latex_lines.append(r"\midrule")

    # 定义配置与组件的对应关系
    component_map = {
        'baseline': r"- & -",
        'mroaw_heur_only': r"\checkmark (Heur.) & -",
        'mroaw_prob_only': r"\checkmark (Prob.) & -",
        'agentic_only': r"- & \checkmark",
        'mroaw_heur_agentic': r"\checkmark (Heur.) & \checkmark",
        'full_fusion': r"\checkmark (Prob.) & \checkmark"
    }

    for config_name in ABLATION_CONFIGS.keys():
        config = ABLATION_CONFIGS[config_name]
        method_name = config['short_name']

        if config_name == 'full_fusion':
            method_name = r"\textbf{" + method_name.replace(' (Ours)', '') + r"} \textit{(Ours)}"

        components = component_map.get(config_name, "- & -")
        row_data = [method_name, components]

        for dataset in DATASETS:
            dataset_df = df[(df['config'] == config_name) & (df['dataset'] == dataset)]

            if len(dataset_df) > 0:
                r10 = dataset_df['recall@10'].values[0]
                r10_str = f"{r10*100:.1f}" if pd.notna(r10) else "-"

                if config_name == 'full_fusion':
                    r10_str = r"\textbf{" + r10_str + "}"

                row_data.append(r10_str)
            else:
                row_data.append("-")

        latex_lines.append(" & ".join(row_data) + r" \\")

    latex_lines.append(r"\bottomrule")
    latex_lines.append(r"\end{tabular}")
    latex_lines.append(r"\end{table}")

    # 保存文件
    output_file = output_dir / 'ablation_analysis.tex'
    with open(output_file, 'w') as f:
        f.write('\n'.join(latex_lines))

    print(f"  ✅ 消融分析表: {output_file}")


def generate_time_cost_table(df, output_dir):
    """
    生成时间开销表
    """
    if 'time_retrieval' not in df.columns or df['time_retrieval'].isna().all():
        print("  ⚠️  无时间数据，跳过时间开销表")
        return

    latex_lines = []

    latex_lines.append(r"\begin{table}[t]")
    latex_lines.append(r"\centering")
    latex_lines.append(r"\caption{Average Retrieval Time Comparison (seconds per query)}")
    latex_lines.append(r"\label{tab:time_cost}")
    latex_lines.append(r"\begin{tabular}{lccc}")
    latex_lines.append(r"\toprule")
    latex_lines.append(r"\textbf{Method} & \textbf{MuSiQue} & \textbf{2WikiMQA} & \textbf{HotpotQA} \\")
    latex_lines.append(r"\midrule")

    for config_name in ABLATION_CONFIGS.keys():
        config = ABLATION_CONFIGS[config_name]
        method_name = config['short_name']

        if config_name == 'full_fusion':
            method_name = r"\textbf{" + method_name.replace(' (Ours)', '') + r"}"

        row_data = [method_name]

        for dataset in DATASETS:
            dataset_df = df[(df['config'] == config_name) & (df['dataset'] == dataset)]

            if len(dataset_df) > 0:
                time_val = dataset_df['time_retrieval'].values[0]
                time_str = f"{time_val:.2f}" if pd.notna(time_val) else "-"
                row_data.append(time_str)
            else:
                row_data.append("-")

        latex_lines.append(" & ".join(row_data) + r" \\")

    latex_lines.append(r"\bottomrule")
    latex_lines.append(r"\end{tabular}")
    latex_lines.append(r"\end{table}")

    # 保存文件
    output_file = output_dir / 'time_cost.tex'
    with open(output_file, 'w') as f:
        f.write('\n'.join(latex_lines))

    print(f"  ✅ 时间开销表: {output_file}")


def generate_improvement_table(df, output_dir):
    """
    生成相对baseline提升表
    """
    latex_lines = []

    latex_lines.append(r"\begin{table}[t]")
    latex_lines.append(r"\centering")
    latex_lines.append(r"\caption{Relative Improvement over Baseline (\%)}")
    latex_lines.append(r"\label{tab:improvement}")
    latex_lines.append(r"\begin{tabular}{lccc}")
    latex_lines.append(r"\toprule")
    latex_lines.append(r"\textbf{Method} & \textbf{MuSiQue} & \textbf{2WikiMQA} & \textbf{HotpotQA} \\")
    latex_lines.append(r"\midrule")

    for config_name in ABLATION_CONFIGS.keys():
        if config_name == 'baseline':
            continue

        config = ABLATION_CONFIGS[config_name]
        method_name = config['short_name']

        if config_name == 'full_fusion':
            method_name = r"\textbf{" + method_name.replace(' (Ours)', '') + r"}"

        row_data = [method_name]

        for dataset in DATASETS:
            baseline_df = df[(df['config'] == 'baseline') & (df['dataset'] == dataset)]
            config_df = df[(df['config'] == config_name) & (df['dataset'] == dataset)]

            if len(baseline_df) > 0 and len(config_df) > 0:
                baseline_r10 = baseline_df['recall@10'].values[0]
                config_r10 = config_df['recall@10'].values[0]

                if pd.notna(baseline_r10) and pd.notna(config_r10) and baseline_r10 > 0:
                    improvement = (config_r10 - baseline_r10) / baseline_r10 * 100
                    imp_str = f"{improvement:+.1f}"

                    if config_name == 'full_fusion':
                        imp_str = r"\textbf{" + imp_str + "}"

                    row_data.append(imp_str)
                else:
                    row_data.append("-")
            else:
                row_data.append("-")

        latex_lines.append(" & ".join(row_data) + r" \\")

    latex_lines.append(r"\bottomrule")
    latex_lines.append(r"\end{tabular}")
    latex_lines.append(r"\end{table}")

    # 保存文件
    output_file = output_dir / 'improvement_table.tex'
    with open(output_file, 'w') as f:
        f.write('\n'.join(latex_lines))

    print(f"  ✅ 提升表: {output_file}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='生成LaTeX表格')
    parser.add_argument(
        '--result-dir',
        default='experiments/ablation_mroaw_agentic/aggregated',
        help='聚合结果目录'
    )
    parser.add_argument(
        '--output-dir',
        default='experiments/ablation_mroaw_agentic/tables',
        help='表格输出目录'
    )

    args = parser.parse_args()

    print("\n" + "="*100)
    print("📄 生成LaTeX表格")
    print("="*100 + "\n")

    # 创建输出目录
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 加载结果
    print("📂 加载数据...")
    df = load_latest_results(args.result_dir)

    if df is None:
        sys.exit(1)

    print("\n📝 生成表格...")

    # 生成各类表格
    generate_main_results_table(df, output_dir)
    generate_ablation_table(df, output_dir)
    generate_time_cost_table(df, output_dir)
    generate_improvement_table(df, output_dir)

    print("\n✅ 表格生成完成！")
    print(f"   输出目录: {output_dir}")
    print("\n💡 使用方法:")
    print("   在LaTeX文档中使用 \\input{path/to/table.tex} 引入表格\n")


if __name__ == '__main__':
    main()
